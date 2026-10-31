"""
AI Travel Agent (Streamlit UI)
 
    pip install -r requirements.txt        # Python 3.11+
    cp mcp_servers.example.json mcp_servers.json   # then edit
    export ANTHROPIC_API_KEY=...
    streamlit run app.py
 
MCP connections are shared by the process; saved conversations are private to each
signed-in user through Supabase row-level security.
"""
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
 
import anthropic
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from agent_core import MCPHub, TravelAgent
from chat_store import ChatStore, ChatStoreError, create_session
 
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
CONFIG_PATH = Path(os.getenv("MCP_CONFIG", Path(__file__).parent / "mcp_servers.json"))
 
st.set_page_config(page_title="🧳 AI Travel Agent", page_icon="🧳", layout="wide")
st.title("🧳 AI Travel Agent")


def get_secret(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    try:
        return st.secrets.get(name)
    except StreamlitSecretNotFoundError:
        return None


def get_anthropic_api_key() -> str | None:
    return get_secret("ANTHROPIC_API_KEY")


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    st.error(
        "Configure SUPABASE_URL and SUPABASE_ANON_KEY in Streamlit Secrets "
        "to enable private saved chats."
    )
    st.stop()

chat_store = ChatStore(SUPABASE_URL, SUPABASE_ANON_KEY)

if "auth_session" in st.session_state:
    auth_session = st.session_state["auth_session"]
    if auth_session["expires_at"] <= time.time() + 60:
        try:
            auth_session = create_session(
                chat_store.refresh(auth_session["refresh_token"])
            )
            st.session_state["auth_session"] = auth_session
        except ChatStoreError as error:
            st.session_state.pop("auth_session", None)
            st.session_state.pop("agent", None)
            st.session_state.pop("active_conversation_id", None)
            st.session_state.pop("saved_conversation_selection", None)
            st.session_state["auth_error"] = (
                f"Your sign-in expired. Please sign in again. ({error})"
            )

if "auth_session" not in st.session_state:
    with st.form("account_form"):
        auth_action = st.radio(
            "Account", ["Sign in", "Create account"], horizontal=True
        )
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button(auth_action, type="primary")
    if error := st.session_state.pop("auth_error", None):
        st.error(error)
    if submitted:
        try:
            if auth_action == "Create account":
                response = chat_store.sign_up(email.strip(), password)
            else:
                response = chat_store.sign_in(email.strip(), password)
            try:
                st.session_state["auth_session"] = create_session(response)
            except ChatStoreError as error:
                st.success(str(error))
                st.stop()
            st.rerun()
        except ChatStoreError as error:
            st.error(str(error))
    st.stop()

auth_session = st.session_state["auth_session"]


@st.cache_resource(show_spinner="Connecting to MCP servers…")
def get_hub() -> MCPHub:
    if CONFIG_PATH.exists():
        servers = json.loads(CONFIG_PATH.read_text())
    else:
        try:
            configured_servers = st.secrets.get("mcp_servers", {})
        except StreamlitSecretNotFoundError:
            configured_servers = {}
        servers = {
            name: {
                key: dict(value) if isinstance(value, Mapping) else value
                for key, value in config.items()
            }
            for name, config in configured_servers.items()
        }
    return MCPHub(servers)


ANTHROPIC_API_KEY = get_anthropic_api_key()
if not ANTHROPIC_API_KEY:
    st.error(
        "Configure ANTHROPIC_API_KEY in your environment or Streamlit secrets, "
        "then restart the app."
    )
    st.stop()

hub = get_hub()
if "agent" not in st.session_state:
    st.session_state.agent = TravelAgent(
        hub, anthropic.Anthropic(api_key=ANTHROPIC_API_KEY), MODEL
    )
    st.session_state.active_conversation_id = None
st.session_state.setdefault("active_conversation_id", None)
agent: TravelAgent = st.session_state.agent


def persist_conversation() -> None:
    if not agent.messages:
        return
    first_user_message = next(
        (
            message["content"]
            for message in agent.messages
            if message["role"] == "user" and isinstance(message["content"], str)
        ),
        "Travel conversation",
    )
    title = " ".join(first_user_message.split())[:80] or "Travel conversation"
    st.session_state.active_conversation_id = chat_store.save_conversation(
        auth_session,
        st.session_state.active_conversation_id,
        title,
        agent.messages,
    )
 
# ------------------------------------------------------------ sidebar
with st.sidebar:
    st.caption(f"Signed in as {auth_session['email']}")
    if st.button("Sign out"):
        try:
            chat_store.sign_out(auth_session)
        except ChatStoreError as error:
            st.session_state["signout_error"] = (
                f"Could not invalidate the remote session: {error}"
            )
        st.session_state.pop("auth_session", None)
        st.session_state.pop("agent", None)
        st.session_state.pop("active_conversation_id", None)
        st.session_state.pop("saved_conversation_selection", None)
        st.session_state.pop("conversation_to_delete", None)
        st.rerun()
    if error := st.session_state.pop("signout_error", None):
        st.warning(error)

    st.subheader("Saved conversations")
    try:
        saved_conversations = chat_store.list_conversations(auth_session)
    except ChatStoreError as error:
        saved_conversations = []
        st.error(f"Could not load saved conversations: {error}")
    saved_by_id = {item["id"]: item["title"] for item in saved_conversations}
    selected_conversation = st.selectbox(
        "Choose a conversation",
        options=[""] + list(saved_by_id),
        format_func=lambda conversation_id: (
            "Select a saved conversation"
            if not conversation_id
            else saved_by_id[conversation_id]
        ),
        key="saved_conversation_selection",
    )
    load_col, delete_col = st.columns(2)
    if load_col.button("Load", disabled=not selected_conversation):
        try:
            saved = chat_store.get_conversation(auth_session, selected_conversation)
            loaded_agent = TravelAgent(
                hub, anthropic.Anthropic(api_key=ANTHROPIC_API_KEY), MODEL
            )
            loaded_agent.messages = saved["messages"]
            st.session_state.agent = loaded_agent
            st.session_state.active_conversation_id = saved["id"]
            st.rerun()
        except ChatStoreError as error:
            st.error(f"Could not load that conversation: {error}")
    if delete_col.button("Delete", disabled=not selected_conversation):
        st.session_state["conversation_to_delete"] = selected_conversation
    if st.session_state.get("conversation_to_delete") == selected_conversation:
        st.warning("Delete this saved conversation permanently?")
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button("Confirm delete", type="primary"):
            try:
                chat_store.delete_conversation(auth_session, selected_conversation)
                if st.session_state.active_conversation_id == selected_conversation:
                    st.session_state.agent = TravelAgent(
                        hub, anthropic.Anthropic(api_key=ANTHROPIC_API_KEY), MODEL
                    )
                    st.session_state.active_conversation_id = None
                st.session_state.pop("conversation_to_delete", None)
                st.session_state.pop("saved_conversation_selection", None)
                st.rerun()
            except ChatStoreError as error:
                st.error(f"Could not delete that conversation: {error}")
        if cancel_col.button("Keep conversation"):
            st.session_state.pop("conversation_to_delete", None)
            st.rerun()

    st.header("MCP connections")
    if not hub.servers:
        st.warning(
            "No MCP servers configured. Chat works, but travel search and booking tools "
            "are unavailable. Add server settings in Streamlit Secrets."
        )
    for name in hub.servers:
        if name in hub.errors:
            st.error(f"❌ {name}: {hub.errors[name]}")
        else:
            tools = {k: v for k, v in hub.tools.items() if v["server"] == name}
            st.success(f"✅ {name} ({len(tools)} tools)")
            with st.expander("tools"):
                for t in tools.values():
                    st.write(("🔒 " if t["write"] else "🔍 ") + t["name"])
    st.caption("🔒 = needs your approval before it runs (name/annotation heuristic: review it).")
    if st.button("🔄 Reconnect servers"):
        hub.close()
        st.cache_resource.clear()
        st.session_state.pop("agent", None)
        st.rerun()
    if st.button("🗑️ New conversation"):
        st.session_state.agent = TravelAgent(
            hub, anthropic.Anthropic(api_key=ANTHROPIC_API_KEY), MODEL
        )
        st.session_state.active_conversation_id = None
        st.rerun()
 
# ------------------------------------------------------------ history
st.caption(
    "Chats are saved privately to your account. Avoid entering passwords, "
    "payment details, or passport numbers. You can delete saved chats from the sidebar."
)
if not agent.messages:
    with st.chat_message("assistant"):
        st.markdown(
            "Hi! I'm your travel agent. Tell me about the trip you have in mind, even if it's "
            "just a rough idea, and I'll ask a few questions so I can plan the whole journey "
            "(flights, stay, transfers, activities) and give you options to choose from."
        )
for m in agent.messages:
    if m["role"] == "user" and isinstance(m["content"], str):
        st.chat_message("user").markdown(m["content"])
    elif m["role"] == "assistant":
        with st.chat_message("assistant"):
            for b in m["content"]:
                if b["type"] == "text" and b["text"].strip():
                    st.markdown(b["text"])
                elif b["type"] == "tool_use":
                    with st.expander(f"🔧 {b['name']}"):
                        st.json(b["input"])
 
# ------------------------------------------------------------ approval gate
if agent.pending:
    st.warning("⚠️ The agent wants to run an action that may book, pay for, or change a reservation. "
               "Review the details carefully.")
    for c in agent.pending["calls"]:
        st.markdown(f"**{c['name']}**")
        st.json(c["input"])
    a, d = st.columns(2)
    if a.button("✅ Approve and run", type="primary"):
        with st.spinner("Working…"):
            try:
                agent.resolve(True)
            except Exception as error:
                st.session_state["last_error"] = (
                    "The action was handled, but the assistant could not finish its "
                    f"response: {type(error).__name__}: {error}"
                )
            try:
                persist_conversation()
            except ChatStoreError as error:
                st.session_state["persistence_error"] = str(error)
        st.rerun()
    if d.button("❌ Decline"):
        with st.spinner("Working…"):
            try:
                agent.resolve(False)
            except Exception as error:
                st.session_state["last_error"] = (
                    f"Could not finish the declined action: {type(error).__name__}: {error}"
                )
            try:
                persist_conversation()
            except ChatStoreError as error:
                st.session_state["persistence_error"] = str(error)
        st.rerun()
    st.stop()

if err := st.session_state.pop("last_error", None):
    st.error(err)
if err := st.session_state.pop("persistence_error", None):
    st.error(f"Your chat could not be saved. {err}")
 
if prompt := st.chat_input("Tell me about your trip…"):
    n = len(agent.messages)
    with st.spinner("Thinking…"):
        try:
            agent.send(prompt)
        except Exception as e:
            del agent.messages[n:]   # roll back the failed turn so you can retry
            agent.pending = None
            st.session_state["last_error"] = (
                f"Your message was not processed. {type(e).__name__}: {e}\n\n"
                f"Your message was: {prompt}"
            )
        else:
            if not agent.pending:
                try:
                    persist_conversation()
                except ChatStoreError as error:
                    st.session_state["persistence_error"] = str(error)
    st.rerun()
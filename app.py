"""
AI Travel Agent (Streamlit UI)
 
    pip install -r requirements.txt        # Python 3.11+
    cp mcp_servers.example.json mcp_servers.json   # then edit
    export ANTHROPIC_API_KEY=...
    streamlit run app.py
 
Single-user local app: MCP connections (and any tokens) are shared by the process.
"""
import json
import os
from pathlib import Path
 
import anthropic
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from agent_core import MCPHub, TravelAgent
 
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
CONFIG_PATH = Path(os.getenv("MCP_CONFIG", Path(__file__).parent / "mcp_servers.json"))
 
st.set_page_config(page_title="🧳 AI Travel Agent", page_icon="🧳", layout="wide")
st.title("🧳 AI Travel Agent")


def get_anthropic_api_key() -> str | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        return api_key

    try:
        return st.secrets.get("ANTHROPIC_API_KEY")
    except StreamlitSecretNotFoundError:
        return None


@st.cache_resource(show_spinner="Connecting to MCP servers…")
def get_hub() -> MCPHub:
    servers = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
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
agent: TravelAgent = st.session_state.agent
 
# ------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("MCP connections")
    if not CONFIG_PATH.exists():
        st.warning(f"No {CONFIG_PATH.name} found. Chat works, but I can't search or book. "
                   "Copy mcp_servers.example.json and edit it.")
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
        st.session_state.pop("agent", None)
        st.rerun()
 
# ------------------------------------------------------------ history
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
            agent.resolve(True)
        st.rerun()
    if d.button("❌ Decline"):
        with st.spinner("Working…"):
            agent.resolve(False)
        st.rerun()
    st.stop()
 
if err := st.session_state.pop("last_error", None):
    st.error(err)
 
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
    st.rerun()
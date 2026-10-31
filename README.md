# AI Travel Agent (Claude + MCP)

A conversational travel agent that
1. **interviews** the traveller (a few questions at a time) and confirms a trip brief,
2. **plans the whole trip** (flights, transfers, stay, local transport, activities, visa/insurance risks) and presents **2-3 complete alternatives with a recommendation**,
3. **books only through MCP tools**, after you choose and after you click **Approve** in the app for each booking-type call.

## Run
    pip install -r requirements.txt          # Python 3.11+
    cp mcp_servers.example.json mcp_servers.json   # edit: add servers/tokens
    export ANTHROPIC_API_KEY=...
    streamlit run app.py

For Streamlit Community Cloud, open the app's **Settings → Secrets**, add:

    ANTHROPIC_API_KEY = "your-Anthropic-API-key"

Save the secret and restart the app. Do not commit API keys to this repository.

## mcp_servers.json
Each key is a server name. Either a remote server:
    "name": {"url": "https://.../mcp", "headers": {"Authorization": "Bearer ${TOKEN}"}}
or a local stdio server:
    "name": {"command": "npx", "args": ["-y", "some-mcp-server"], "env": {"KEY": "${KEY}"}}
`${VAR}` is expanded from your environment.

## Things to know
- **Booking needs a booking-capable server.** Many travel MCP servers only search (e.g. redirect you to book on their site). If no connected tool can book something, the agent says so. Check each vendor's docs and terms; I have not verified their auth or booking behaviour.
- **OAuth servers:** several hosted servers use OAuth. This app does not run an OAuth flow; supply a bearer token you obtained, or self-host/stdio if the vendor offers it.
- **Real money.** Start with sandbox/test credentials. The approval gate is a safety net, not a guarantee.
- **Approval heuristic.** Tools are flagged "needs approval" via MCP `readOnlyHint` when present, else by name (book/cancel/pay/...; get_/search_/list_ prefixes are read-only). Review the 🔒/🔍 list in the sidebar.
- Tool results are truncated at 12,000 characters; very long conversations may need trimming.
- Single-user local app: connections and tokens are shared across browser sessions.

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

## Saved chats (Supabase Free)
The app supports email accounts and private saved conversations using Supabase.
The Free plan is $0 within its quotas; Supabase may pause a free project after a
week of inactivity. Check [current limits and pricing](https://supabase.com/pricing).

1. Create a Supabase project on the Free plan.
2. In the Supabase SQL Editor, run the contents of `supabase_schema.sql`. This
   creates the conversations table and row-level security policies so each account
   can only read, change, or delete its own chats.
3. In Supabase **Project Settings → API**, copy the Project URL and the public
   `anon` key (or publishable key). Never use or expose the `service_role` key.
4. In Streamlit Community Cloud, open the app's **Manage app → Settings → Secrets**
   and add these beside your existing `ANTHROPIC_API_KEY`:

       SUPABASE_URL = "https://your-project-id.supabase.co"
       SUPABASE_ANON_KEY = "your-supabase-public-anon-key"

5. Upload the updated `app.py` and the new `chat_store.py` to your GitHub repository
   and commit them. The SQL file is needed to configure Supabase; it can also be
   committed for reference.
6. Wait for Streamlit to redeploy. Create an account with an email address, confirm
   it if Supabase requests confirmation, then sign in. Chats save automatically;
   use the sidebar to load or delete them.

The app sends saved conversation content to your Supabase database. Users share the
same app-level Anthropic and MCP credentials, but Supabase row-level security keeps
their saved chat records separate. Do not store passwords, payment details, or
passport numbers in chat.

To enable hosted MCP servers, add their configuration to the same Secrets editor.
Start with the flight-search server from the example configuration:

    [mcp_servers.ignav_flights_search_only]
    url = "https://ignav.com/mcp"

The `travelcode` and `letsfg` servers also need credentials from their providers.
Add each under its own `[mcp_servers.<name>]` table, and add the provider-required
authorization header under `[mcp_servers.<name>.headers]`. Use the exact format
the provider specifies; do not use placeholder tokens. Never share screenshots
that show real tokens. Save the settings and click **Reconnect servers** in the
app (or reboot it). The local `mcp_servers.json` file, when present, takes
precedence over the Streamlit Secrets configuration.

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
- **Payments.** Complete payment only on the travel provider's checkout page. The app does not collect or store card/UPI details or remember payment methods. Use only payment options the provider explicitly offers.
- **Saved chats.** Conversation history is stored in Supabase and is available to the signed-in account until deleted. Free-tier usage limits and inactivity pauses apply.
- **Approval heuristic.** Tools are flagged "needs approval" via MCP `readOnlyHint` when present, else by name (book/cancel/pay/...; get_/search_/list_ prefixes are read-only). Review the 🔒/🔍 list in the sidebar.
- Tool results are truncated at 12,000 characters; very long conversations may need trimming.
- MCP server connections and tokens are shared by the app process; saved chats are scoped to each signed-in account.

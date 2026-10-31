"""
Travel-agent core: MCP hub + conversational tool-use loop with a human
approval gate for booking-type tools. No Streamlit imports, so it is testable.
"""
import asyncio
import os
import re
import threading
from contextlib import AsyncExitStack
from datetime import date

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

MAX_RESULT_CHARS = 12000
MAX_STEPS = 15

READ_PREFIX = re.compile(r"^(get|search|list|find|check|resolve|present|read|fetch|lookup|query|show)[_-]", re.I)
WRITE_WORDS = re.compile(
    r"(book|cancel|order|pay|purchase|reserve|checkout|modify|amend|confirm|hold|ticket|issue|refund)", re.I
)

SYSTEM_PROMPT = """You are an experienced, proactive travel agent. Today's date is {today}.

You work in three phases.

1) DISCOVERY (always first)
- Interview the traveller before searching anything. Ask at most 3 focused questions per message, conversationally, building on earlier answers.
- Cover, as relevant: purpose/occasion; origin; destination(s) or open to suggestions; dates and their flexibility; who is travelling (number, ages, relationships, nationalities/passports for visa checks); budget (total, and what can flex); pace and interests; accommodation style, area and must-have amenities; flight preferences (cabin, stops, times, airlines, loyalty programmes, baggage); getting around at the destination; food, accessibility and medical needs; deal-breakers; anything already booked.
- When you have enough, play back a short TRIP BRIEF and ask the traveller to confirm or correct it. Do not start searching until the brief is confirmed, unless the traveller explicitly tells you to go ahead.

2) PLANNING (whole-trip logistics, never parts in isolation)
- Use your tools to research and price real options. Plan end to end: flights (arrival/departure times against hotel check-in/out), airport transfers, where to stay relative to activities, local transport, sequencing and buffers, visa/entry rules, insurance, currency and payment, and a plan B for risks (tight connections, weather, strikes).
- Present 2-3 genuinely different complete trip options (for example Best value / Balanced / Most comfortable or fastest). Cover every leg, use real prices from your tools, and include a compact comparison table of trade-offs (total cost, travel time, flexibility/refundability, risks).
- Then give a clear recommendation and say why it fits THIS traveller. Invite changes and iterate.

3) BOOKING (only through the connected MCP tools)
- You can only book with the booking tools you have been given. If no connected tool can book a component, say so plainly and give the link or next step instead. Never pretend.
- Book only after the traveller has chosen specific options and explicitly said to book. Before the first booking call, restate exactly what will be booked: itinerary, traveller names as on passports, price, currency, fare rules/refundability and total. Collect any missing traveller details at that moment.
- Book one component at a time, in an order that limits risk (flexible or refundable items first; confirm flights before non-refundable hotels). Booking calls also require the traveller's approval in the app; that is expected.
- Never ask for full card numbers, CVVs or passwords in chat. If a tool returns a payment or checkout link, give it to the traveller.
- After each booking, report the confirmation reference exactly as returned. If a call fails or the result is unclear, stop, tell the traveller, and do not retry blindly.

RULES
- Never invent prices, availability, schedules, IDs or confirmation numbers. Use only what tools return; label anything else as an estimate or general knowledge.
- Do not say something is booked unless a tool result clearly says so.
- Visa, health and entry rules change: give your best understanding and tell the traveller to verify with official sources.
- Be concise, warm and organised. Use short tables for comparisons. Ask before assuming anything that costs money.
"""


def is_write_tool(name: str, annotations) -> bool:
    """Heuristic: does this tool book/pay/cancel/modify? Errs on the side of asking."""
    ro = getattr(annotations, "readOnlyHint", None) if annotations else None
    if ro is True:
        return False
    if ro is False:
        return True
    if READ_PREFIX.match(name):
        return False
    return bool(WRITE_WORDS.search(name))


class MCPHub:
    """Keeps MCP sessions alive on a background asyncio loop (Python 3.11+)."""

    def __init__(self, servers: dict):
        self.servers = servers
        self.sessions: dict = {}
        self.tools: dict = {}   # api_name -> {server, name, description, schema, write}
        self.errors: dict = {}
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self._ready.wait(timeout=180)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        self._stop = asyncio.Event()
        async with AsyncExitStack() as stack:
            for name, cfg in self.servers.items():
                try:
                    await self._connect(stack, name, cfg)
                except Exception as e:
                    while isinstance(e, BaseExceptionGroup) and e.exceptions:
                        e = e.exceptions[0]
                    self.errors[name] = f"{type(e).__name__}: {e}"
            self._ready.set()
            await self._stop.wait()

    async def _connect(self, stack: AsyncExitStack, name: str, cfg: dict):
        s = AsyncExitStack()
        try:
            async with asyncio.timeout(60):
                if "url" in cfg:
                    headers = {k: os.path.expandvars(v) for k, v in cfg.get("headers", {}).items()}
                    read, write, _ = await s.enter_async_context(
                        streamablehttp_client(cfg["url"], headers=headers or None)
                    )
                else:
                    env = {k: os.path.expandvars(v) for k, v in cfg.get("env", {}).items()}
                    params = StdioServerParameters(
                        command=cfg["command"], args=cfg.get("args", []), env={**os.environ, **env}
                    )
                    read, write = await s.enter_async_context(stdio_client(params))
                session = await s.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
        except BaseException as first:
            # A failed connection can surface as a cancellation; closing the stack
            # lets anyio convert it into the real error (e.g. ConnectError).
            try:
                await s.aclose()
            except BaseException as real:
                if isinstance(real, Exception):
                    raise real from None
            if isinstance(first, Exception):
                raise
            raise ConnectionError(f"could not connect to {name} (cancelled or unreachable)") from None
        stack.push_async_callback(s.aclose)
        self.sessions[name] = session
        for t in listed.tools:
            api = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{name}__{t.name}")[:64]
            self.tools[api] = {
                "server": name,
                "name": t.name,
                "description": t.description or "",
                "schema": t.inputSchema or {"type": "object", "properties": {}},
                "write": is_write_tool(t.name, t.annotations),
            }

    def anthropic_tools(self) -> list:
        return [
            {"name": api, "description": (t["description"] or t["name"])[:1000], "input_schema": t["schema"]}
            for api, t in self.tools.items()
        ]

    def needs_approval(self, api_name: str) -> bool:
        return self.tools.get(api_name, {}).get("write", True)

    async def _call(self, t: dict, args: dict):
        res = await self.sessions[t["server"]].call_tool(t["name"], args)
        text = "\n".join(getattr(c, "text", None) or f"[{c.type} content omitted]" for c in res.content)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n…[result truncated]"
        return (text or "(empty result)"), bool(res.isError)

    def call(self, api_name: str, args: dict):
        if api_name not in self.tools:
            return f"Unknown tool {api_name}", True
        fut = asyncio.run_coroutine_threadsafe(self._call(self.tools[api_name], args), self.loop)
        return fut.result(timeout=180)

    def close(self):
        if self._stop is not None:
            self.loop.call_soon_threadsafe(self._stop.set)


class TravelAgent:
    """Conversation state + tool loop. Booking-type tools pause for human approval."""

    def __init__(self, hub: MCPHub, client, model: str):
        self.hub, self.client, self.model = hub, client, model
        self.messages: list = []
        self.pending: dict | None = None   # {"calls": [...], "results": {id: block}, "order": [ids]}

    # -- public API
    def send(self, text: str):
        if self.pending:
            raise RuntimeError("Resolve the pending approval first.")
        self.messages.append({"role": "user", "content": text})
        self._advance()

    def resolve(self, approved: bool):
        p, self.pending = self.pending, None
        results = dict(p["results"])
        for c in p["calls"]:
            if approved:
                results[c["id"]] = self._run_tool(c)
            else:
                results[c["id"]] = {
                    "type": "tool_result", "tool_use_id": c["id"], "is_error": False,
                    "content": "The traveller DECLINED this action. Do not retry it; ask what they would like to change.",
                }
        self.messages.append({"role": "user", "content": [results[i] for i in p["order"]]})
        self._advance()

    # -- internals
    def _run_tool(self, call: dict) -> dict:
        try:
            text, err = self.hub.call(call["name"], call["input"])
        except Exception as e:
            text, err = f"Tool call failed: {e}", True
        return {"type": "tool_result", "tool_use_id": call["id"], "content": text, "is_error": err}

    def _advance(self):
        for _ in range(MAX_STEPS):
            kwargs = dict(
                model=self.model, max_tokens=4096, messages=self.messages,
                system=SYSTEM_PROMPT.format(today=date.today().strftime("%A, %d %B %Y")),
            )
            tools = self.hub.anthropic_tools()
            if tools:
                kwargs["tools"] = tools
            resp = self.client.messages.create(**kwargs)

            blocks, calls = [], []
            for b in resp.content:
                if b.type == "text":
                    blocks.append({"type": "text", "text": b.text})
                elif b.type == "tool_use":
                    blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                    calls.append({"id": b.id, "name": b.name, "input": b.input})
            self.messages.append({"role": "assistant", "content": blocks})
            if resp.stop_reason != "tool_use" or not calls:
                return

            results, gated = {}, []
            for c in calls:
                if self.hub.needs_approval(c["name"]):
                    gated.append(c)
                else:
                    results[c["id"]] = self._run_tool(c)
            if gated:
                self.pending = {"calls": gated, "results": results, "order": [c["id"] for c in calls]}
                return
            self.messages.append({"role": "user", "content": [results[c["id"]] for c in calls]})
        self.messages.append({"role": "assistant", "content": [
            {"type": "text", "text": "I hit my step limit for this turn. Tell me how you'd like to continue."}]})

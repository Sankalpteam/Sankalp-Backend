# Sankalpa Open-Source End-to-End Capability Coverage

**Development-only design contract — 20 November 2026.** This covers every
existing registered tool family, not merely app-store Plugins. It is an inventory
of what is implemented versus what still requires a verified adapter; it does
not assert that connecting arbitrary MCP servers is safe or working.

## Single path to build (not an unconditional production switch)

User Web/Android/voice/channel request → authenticated principal and turn claim →
trusted tool discovery → official LangGraph StateGraph execution → tool-specific
user-scoped authorization → official interrupt + durable checkpoint for a
supported write → authenticated approval review → one-shot Command(resume) →
bounded execution/verification → run/result state and UI recovery.

The LangGraph Python packages and checkpointers already power the **isolated
single-write** approval path. Multi-write and dependent multi-step checkpoint
resume are NOT yet done. Normal chat keeps its legacy flow until proven parity.
The default chat-LangGraph switch is OFF.

## Truthful capability matrix

| Existing source | What's implemented now | LangGraph opt-in mode | Remaining work |
|---|---|---|---|
| Native tools: calculator, text, JSON, code analysis, transform, data analysis, schedule validation | ToolSpec plus registered executor | Read-only safe tools allowed | Device/provider E2E and quality parity |
| GitHub | Read tools and existing write executors with connector authorization | Registered read tools allowed; one selected write requiring approval can pause only when flags are enabled | Migrate full write policy without bypassing existing confirmed actions |
| Instagram | Registered read APIs and permission metadata | Registered reads allowed, if authenticated connected access | Connector credential E2E; future writes separately reviewed |
| Gmail / Zoho Mail | Dedicated authenticated email tools, pending confirmation and encryption | Not yet a registered graph executor; still legacy-only | Official adapter, model routing parity, exact message preview, owner-bound permission and resume |
| Composio | Optional official SDK Gmail read, optionally via official MCP Python SDK | Not yet a registered graph executor; legacy-tool-loop read only | Trusted explicit MCP adapter and SDK compatibility, account auth, bounded execution; no send/delete by default |
| Web research | Existing public-web tool loop | Not yet the same graph executor | Official read-only adapter, citations and evidence parity |
| Code sandbox | Existing guarded executor | Blocked in chat opt-in despite approval metadata | Separate execution consent, isolation and resource tests |
| Voice and additional social/media services | Their existing endpoint/channel implementations | Not automatically graph-executable | Individual adapters and end-to-end release gates |

**Runtime enforcement:** `app/ai/execution/agent_tool_capabilities.py`
derives native tool policy from `TOOL_SPECS` and actual `TOOLS` executors.
Missing registrations, unknown MCP tools, undeclared risk and unapproved writes
are blocked. Registered connector reads are admitted as candidates, **not**
granted access: the existing executor still verifies owner, connector
capability and token scope before dispatch.

**Migration order:** (1) harden legacy pending/claim and tests, (2) audit
capabilities across all sources, (3) add one reviewed official adapter at a
time, (4) verify multi-step approval lifecycle, (5) Web/Android/voice
recovery and native UI, (6) real connected E2E and resource tests, (7)
explicitly authorized rollout.

Do not auto-import an MCP server's tools based solely on readOnlyHint or other
untrusted annotations: MCP annotations are hints, not an authorization grant.
Discovery must be backed by operator-controlled allowed endpoints, vetted tool
names/schemas, authenticated user mapping, server-checked risk, timeouts and
no automatic write retries. Prefer official maintained MCP SDK and
langchain-mcp-adapters compatibility rather than rewriting the protocol.

## Operating guardrails

- No user-supplied URL/host, prompt, model-generated tool name or remote MCP
  metadata may waive authenticated authorization, tool policy or approval.
- Writes are never retried automatically; a crash after a claim requires
  reconciliation. A queued draft is not a successful external write.
- Preserve all current Gmail/Zoho and GitHub legacy approvals until the
  official graph adapter has matching tests and can be switched explicitly.
- Do not enable flags, merge production main, install local MCP daemons, or
  deploy on the shared 2 GiB AWS host without separate approval and headroom
  checks.

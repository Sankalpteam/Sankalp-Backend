# Sankalpa AI — Open-source replacement checkpoint and release gates

**Status: ACTIVE DEVELOPMENT ONLY — NOT READY TO MERGE/DEPLOY**

This is a factual checklist against the fifth branch and current CI. A Git
branch is a code revision, NOT another AWS instance. Production main currently
runs on the **single existing 2 GiB RAM AWS server**. Nothing here provisions
or deploys a second server, database, LiteLLM daemon or connector stack.

## Implemented, tested on GitHub Actions

| Scope | Evidence | Status |
| --- | --- | --- |
| Official LangGraph StateGraph runtime | `requirements.txt`, `app/ai/execution/langgraph_orchestrator.py` | Source replacement DONE; request-scoped |
| Synchronous legacy agent scheduler | `app/ai/execution/langgraph_sync_orchestrator.py` | DONE, retains old tool and response contracts |
| Gemini tool rounds | `app/ai/gateway/gemini_tool_graph.py` | DONE, model/tools graph, dynamically discovered tools |
| Groq fallback tool rounds | `app/ai/gateway/groq_tool_graph.py` | DONE, model/tools graph, shared secure dispatcher |
| Legacy /api/agent model selection | `app/ai/agents/model_planner.py` | PARTIAL; one bounded read, **disabled by default** pending live tests |
| Identity / approval payload security | `app/ai/execution/tool_loop.py` | Authenticated backend replaces model-supplied identities/tokens |
| Single-host deployment guards | `scripts/ops/run_server.py`, `scripts/ops/deploy.sh` | Built + CI-tested on feature branch, **not active on live AWS** |
| RAM measurement | `scripts/ops/memory_report.py`, `profile_langgraph_memory.py` | Offline tools + CI instrumentation, **NOT AWS measurement** |
| Original upstream attribution | `third_party/licenses/fastapi-langgraph-template.LICENSE` | MIT license preserved |
| Optional Composio Gmail native adapter | `app/ai/execution/composio_bridge.py`, `requirements-composio.txt` | SDK 0.33.0 CI-tested; live account not verified, flag OFF |
| Optional official MCP v2 client | `app/ai/execution/composio_mcp.py`, `requirements-mcp.txt` | Upstream SDK 2.3.0 CI-tested, strict one-Gmail-read allowlist; flag OFF |

The official LangGraph package runs actual upstream code; the provider-specific
data adapters are local integrations, not a complete vendoring of third-party
template repos. Never claim 100% replacement just because tests are green.

## Blockers that cannot be declared complete from a repository-only run

1. **Real AWS RAM / CPU and DB measurements**: No SSH/host telemetry is
   available to this development run. The **existing main app and database
   already consume part of the only 2 GiB**. CI reports a *new, separate
   runner process*; its memory numbers cannot be used as AWS free RAM.
   The auditing module is currently only on the feature branch, not live main.
   Production OS commands are documented in `deploy/DEPLOY.md`.
2. **Composio OAuth and MCP**: The reviewed Composio Python SDK is MIT-licensed
   and requires a Composio project key/hosted session to execute tools
   (https://github.com/ComposioHQ/composio/tree/next/python). Its standard
   hosted MCP flow is not a purely self-hosted OAuth store. **Do not upload
   existing Gmail/Zoho/GitHub OAuth credentials or transfer connected account
   control to a third party** without a separate data-custody/security decision.
   Existing server-side encrypted, user-scoped connectors remain authoritative.
   The official MCP Python SDK v2.3.0
   (https://github.com/modelcontextprotocol/python-sdk) is now used by an
   **optional outbound client** inside the existing FastAPI process. It connects
   to a per-authenticated-user Composio session and can invoke only
   GMAIL_FETCH_EMAILS, with read-only direct-tool configuration, verified
   credential destination, strict tool-list verification and bounded output.
   The feature defaults OFF; no arbitrary MCP tool execution is enabled, no
   incoming public MCP endpoint is exposed and no additional AWS service runs.
   Real user consent, SDK execution and Composio service agreements remain
   release blockers.
3. **LiteLLM B2B proxy/virtual keys**: The LiteLLM core is MIT-licensed; some
   enterprise paths use a different license
   (https://github.com/BerriAI/litellm/blob/main/LICENSE).
   Virtual keys, budgets, per-tenant usage accounting, pricing, billing,
   abuse protection and Postgres schema require a separately tested B2B
   gateway. **No LiteLLM proxy is running**, and it must not be started
   alongside production main on the single 2 GiB machine. First benchmark
   dependencies and find an in-process or controlled cutover topology that
   does not take down current users. The B2B gateway is NOT ready to sell.
4. **Live provider and OAuth integration tests**: CI uses mocked Gemini,
   Groq, GitHub, Gmail and Zoho actions. Real provider credentials/accounts,
   approval and OAuth lifecycle must be tested in an approved isolated
   environment, with harmless reads before any controlled writes.
5. **Web / Android end-to-end + streaming + load**: The feature branch is not
   deployed; no live streaming/stream-retry, connection loss, reconnection,
   mobile client and concurrency evidence can be claimed from CI alone.
6. **LangGraph durable human approval**: Upstream `interrupt()`,
   `Command(resume=...)` and official SQLite/PostgreSQL checkpointers now
   govern the opt-in, **single-action** approval workflow. The app retains
   one atomic owner-scoped status claim to prevent provider-write replay
   after a crash. This is NOT general multi-step graph resume. Feature OFF
   by default; client consent UI and real connected-provider E2E are pending.
7. **Legacy deterministic planner**: Kept deliberately for compatibility with
   documented `/api/agent` tools/results; opt-in model planning covers only
   previously-unmatched authenticated **read** requests. A full cutover is
   blocked until equivalent tool-schema, prompt-injection, multilingual,
   approval, memory, chain and existing client contract tests pass.

## Single-host rollout gate

- [ ] Read-only `free -m` + process RSS + database memory survey on live AWS.
- [x] CI isolated **import** RSS for optional Composio 0.33.0 and MCP 2.3.0:
      MCP+Composio SDK peak ~194 MiB on GitHub Actions, not the live AWS host.
- [ ] Measure real 2 GiB **combined** production RSS, model + database load
      and concurrency before enabling any optional SDK.
- [ ] Authenticate and run model selection against the user's *test* Gemini
      quota; keep disabled unless provider tool schemas and cost are verified.
- [ ] Audit source licenses and data custody before Composio / LiteLLM activation.
- [ ] Confirm production migration, backup, rollback, OAuth data and schema safety.
- [ ] Web + Android real endpoint E2E and rate-limit / disconnect tests.
- [ ] Explicit human approval for the eventual merge and one-host production cutover.

No feature branch is merged by this workflow. The production deployment script
now refuses a non-`main` branch and uses **fast-forward-only**, preventing a
`git pull origin feature` from implicitly merging a feature into the live
main checkout. The two backup branches and earlier cleanup branch are unchanged.

## Official MCP client source checkpoint — 2026-11-19

- Latest CI: https://github.com/Sankalpteam/Sankalp-Backend/actions/runs/44471087745
  (2,251 passed; 20 skipped; MCP SDK, Composio SDK, PostgreSQL and security smoke green).
- Changed only `feat/open-source-langgraph-redesign-20261118`. Native Gmail, GitHub and Zoho OAuth/token stores remain authoritative.
- The MCP path is OFF by default and does not start a daemon. It is an additional
  **in-process client**, not a full generic MCP connector marketplace and not a
  replacement for OAuth consent.
- Live provider reads, opt-in rollout, actual AWS server memory and Web/Android E2E remain pending.
- No merge, production deploy, server restart or changes to main/backups.


## Upstream LangGraph HITL replacement — development checkpoint (2026-11-20)

- Replaced the temporary custom `durable_approval.py` state machine and
  `0005_durable_agent_approvals` ledger migration with the official
  `StateGraph` + `interrupt()` + `Command(resume=...)` and maintained
  `AsyncSqliteSaver` / `AsyncPostgresSaver` backends in
  `app/ai/execution/langgraph_approval.py`.
- SQLite checkpoints share the **existing application database file**;
  PostgreSQL checkpoints share the configured database. There is no new
  server, daemon, SaaS or separate hosted database. Both checkpoint libraries
  are upstream dependencies (SQLite 3.1.3, PostgreSQL 3.2.1) and MIT-licensed.
- The upstream checkpointer owns workflow pause and resume. The existing
  application `agent_runs` record remains authoritative for user ownership
  and original tool arguments. A small SQL compare-and-set from
  `awaiting_approval` to `approval_claimed` is deliberately retained:
  LangGraph checkpoint replay alone does **not** guarantee exactly-once
  side effects on remote GitHub/Gmail providers.
- The pure review graph pauses **before** side effects. Resume validates
  the authenticated owner and hashes the exact stored action, then consumes
  the original graph checkpoint using `Command(resume=...)`. The existing
  authenticated LangGraph tool executor is invoked only after the one-time
  claim and successful human approval.
- Only one isolated, blocked, authorized write is supported. Multi-step graph
  interrupt chains, streaming tools and arbitrary model-written actions are
  still outside this gate. `INDOONE_AGENT_DURABLE_APPROVAL_ENABLED=false`
  remains the default; unconfigured deployments do not activate it.
- An uncertain provider outcome or crash after claim can **never** trigger
  automatic replay; owner-specific manual reconciliation remains required.
  The resulting guarantee is **at most one attempted dispatch per run**, NOT
  guaranteed exactly-once external delivery.
- CI tests exercise the real official SQLite interrupt and persisted resume
  across connections, owner isolation, tampering, expiry, deny, concurrent
  duplicate submission and uncertain results. PostgreSQL end-to-end checkpoint
  and real connected-account tests are still required release gates.
- **DO NOT MERGE OR DEPLOY** without additional Web/Android consent UX,
  provider E2E, PostgreSQL checkpointer validation where applicable,
  security review and actual single-host 2 GiB memory/load measurements.

## Step 3 — explicit Chat → LangGraph opt-in bridge (2026-11-20)

This is development only, not an automatic switch of live chat. Source:
app/ai/execution/chat_agent_bridge.py, app/api/chat/chat.py,
app/api/chat/platform.py, app/ai/agents/agent_async.py.

- INDOONE_CHAT_LANGGRAPH_ENABLED=false by default. An authenticated client must send execution_mode=langgraph AND a stable client_turn_id. Normal chat routes preserve their existing behavior.
- GET /api/chat/agent-mode exposes the authenticated user's server flag; older deployed servers advertise nothing.
- Only registry risk=read tools without an approval requirement execute immediately. A single risk=write tool requiring official consent may ONLY stage a LangGraph interrupt when the durable approval flag is ON; no write is dispatched without human approval.
- The chat bridge cannot accept approval_tokens or model/client-authorized tool names. Legacy CONFIRM/CANCEL messages remain part of the separate existing chat confirmation workflow.
- Blocking and streaming both use the existing durable client_turn_id admission. No agent stream-to-blocking retry or automatic replay on timeout.
- Chat responses expose agent_run_id / agent_status only in explicitly selected mode. Web/Android Draft PR #277 uses those fields to refresh the owner-scoped approval review UI.
- Unsupported attachments, standalone confirmations and missing turn IDs are rejected before dispatch. Failed or blocked tool execution never becomes a success claim.
- CI includes an actual SQLite LangGraph interrupt started from the chat endpoint, owner-only approval review and Command(resume=deny) via HTTP; no external provider write.
- NOT DONE: transparent automatic routing, existing GitHub/email action migration, multi-step checkpoint resumes, connected-provider and device E2E, single-host 2 GiB AWS capacity, release rollback.
- Feature OFF by default. DRAFT. DO NOT MERGE OR DEPLOY.

## Legacy Gmail/GitHub approval race hardening (2026-11-20)

- Audit found the older CONFIRM/CANCEL handlers in app/plugins/github/chat_tools.py and app/plugins/email/email_assistant.py read a saved proposal with get_pending_* and then deleted it in a separate database operation before executing. Parallel confirmations could both see the proposal and attempt the same provider write.
- A minimal storage-level single-use safety guard consume_pending_email_action() now uses the EXISTING DB transaction: SQLite BEGIN IMMEDIATE, PostgreSQL SELECT ... FOR UPDATE. It confirms the authenticated owner, conversation ID, integration, action type, encrypted payload, created_at and expiration all still match the previewed proposal; expiration is rechecked under lock before claim.
- GitHub uses consume_pending_github_action(); Zoho email uses consume_pending_email_action(). Only a successful atomic claim may reach an external write. All confirmations, cancellations, recipient-completion and malformed-payload cleanup check their original row, so neither a stale confirm nor a stale cancellation can erase a NEWER proposal.
- Pending rows are consumed BEFORE side effects, meaning process crashes or timeouts cannot re-dispatch that particular stored action. This guarantees at most one attempted dispatch per pending proposal, NOT exactly-once remote delivery. Uncertain outcomes require checking the provider; no automatic replay.
- Expired pending-record cleanup now deletes only the matching expired snapshot, not a concurrently replaced new proposal. Legacy GitHub delete helper also uses exact atomic claim instead of read-then-generic-delete.
- Tests: 8-thread SQLite concurrent claims, two simultaneous GitHub CONFIRMs, two simultaneous Zoho mail CONFIRMs, cancel-vs-confirm racing, per-user/per-conversation isolation, replacement, expiry and wrong-integration rejection. A dedicated CI PostgreSQL job checks competing claims on actual PostgreSQL 16.
- These changes are a SECURITY HARDENING of existing pending confirmation transport, not a new hand-built workflow or a claim that legacy Gmail/GitHub proposals now use upstream LangGraph interrupts. The new opt-in /api/agent approvals continue to use official LangGraph interrupt/resume/checkpointers. Full migration of normal /api/chat write proposals and live connected-provider E2E remain TODO.
- Still OFF for new LangGraph chat mode. No AWS work, no main merge, no deployment, no new service or second database.

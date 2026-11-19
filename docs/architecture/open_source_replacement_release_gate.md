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
6. **LangGraph durable human approval**: The upstream `ask_human` reference
   tool is present but NOT wired to side-effecting tools. Correct resume
   needs stable authenticated user/thread ownership, trusted approval tokens,
   deduplication and idempotency across crashes and multiple requests.
   In-memory/request-only graphs remain safe without resumable writes.
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

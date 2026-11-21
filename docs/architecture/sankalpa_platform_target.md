# Sankalpa AI platform architecture — development baseline

Status: **development branch only**. This is a target design, not an assertion
that all features are already shipped.

## Rollback anchors (2026-11-18)

These backup branches point to the exact source commits shown below. GitHub branches
are mutable refs, **not immutable tags**: do not force-push, commit to or delete
them until the release and rollback window are closed.

| Purpose | Branch | Source commit |
| --- | --- | --- |
| Production baseline | `backup/main-before-architecture-20261118` | `4dd38342ff4b0c7eb1ac8d836642e4a6c8202e1d` |
| Prior cleanup work | `backup/cleanup-before-architecture-20261118` | `6dc0586ed1facc7a4ac2b6a0d18b5165d3af38ab` |

Development stays on `fix/gemini-groq-tools-safe-cleanup-20261118`.
The `main` branch is not an automatic deployment destination for this work.

Rollback procedure: freeze new writes, diagnose schema/data compatibility,
select the known commit, restore using a **new** recovery branch/PR, run tests
and staging checks, and deploy with explicit approval. Restoring old code
does **not** automatically reverse applied database migrations or undo
third-party GitHub/email writes.

## Requirements from the AI platform design discussion

- One owned backend repository, **FastAPI** and shared authenticated APIs for
  Android, Web and future business clients.
- **Gemini** is the primary AI provider. **Groq** is the configured secondary
  provider. Avoid local CPU/GPU inference and training or Azure OpenAI paths.
- A single tool registry and protected executor for GitHub, Gmail, Zoho Mail,
  Web Research and future plugins. All user-scoped OAuth tokens stay server-side.
- Distinguish read-only tools from writes; use explicit approval and durable
  idempotency/confirmation for writes. Provider output is untrusted evidence,
  not a claim of successful execution.
- Secure, user-scoped conversation history, status and attached files.
- Future **B2B API gateway** for branded API keys, metering, budgets and
  billing, isolated from the mobile assistant and its OAuth permissions.
- Target deployment: AWS with a **2 GiB RAM constraint**, to be *measured*
  with real traffic and concurrency; never assume a fixed memory footprint.

## Architecture boundaries

```text
Web / Android / Future Business Client
            |
       FastAPI API
   Auth + admission + quotas
            |
      Agent orchestration
   Tool registry + approvals
         /       \
    Gemini ---> Groq (safe fallback)
            |
     Backend tool executor
  Gmail / Zoho / GitHub / Web
            |
         Data store
  Users + OAuth + turns + usage

Separate future B2B API gateway
    Keys + model alias + usage ledger
    independent of user OAuth tokens
```

## Migration sequence

1. **Preserve and verify:** take rollback snapshots, inventory dependencies,
   enforce one primary-provider policy and lock down API contracts in tests.
2. **Unify agents:** migrate `/api/agent` away from the old heuristic planner
   without breaking response contracts, permissions, structured tool outcomes,
   chain execution or existing coding utilities.
3. **Tool and connector reliability:** verify OAuth ownership, token refresh,
   provider failures, approvals, server-side status reconciliation and
   tool-result grounding; use a single executor for Gemini and Groq.
4. **App E2E:** verify Web/Android stream, chat history, status, attachments,
   network interruption and connected GitHub/Gmail/Zoho operations in staging.
5. **B2B gateway decision:** compare a measured LiteLLM adapter versus a thin
   native gateway. Add a service only if compatibility, security, deployment
   footprint and provider usage terms permit it.
6. **Release gate:** staged deployment, performance/load, 2 GiB RSS budget,
   schema migration and rollback rehearsals, secrets scan and approval to merge.

## External framework decisions

Do **not** blindly copy third-party repository templates or install all of
LangGraph, LiteLLM and Composio at once. FastAPI, authorization, tools and
Gemini/Groq dispatch already exist. Evaluate LangGraph for multi-step
orchestration and Composio/Nango/MCP for connector integration behind narrow
interfaces and isolated tests. Measure memory use and retain the existing
working paths until the replacement passes equivalent contracts.

No percentage success guarantee or fixed '800 MB free RAM' claim follows from
a design document. Test reliability using actual provider calls in staging.

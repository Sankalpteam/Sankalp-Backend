# Imported open-source LangGraph engine: source and license

## Official runtime

The production workflow runtime is the **official** `langgraph` package,
installed from PyPI as `langgraph>=1.0.2,<2.0`. This executes the actual,
maintained upstream StateGraph source code rather than a local reimplementation
of the scheduler. LangGraph upstream is MIT licensed:
https://github.com/langchain-ai/langgraph

## Template code reused verbatim

The file `app/ai/execution/langgraph_tools/ask_human.py` is an exact source
copy of the human-in-the-loop tool from:

- Repository: https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template
- Revision: `fe052a7c6acf692c05ca0e0a50c0ff2ffba448b7`
- Source file: `app/core/langgraph/tools/ask_human.py`
- License: MIT; preserved under `third_party/licenses/fastapi-langgraph-template.LICENSE`.

**Not yet exposed to the model.** Existing server-issued approval tokens remain
the only way to authorize sensitive writes. A human interrupt by itself is
not sufficient to authorize a write; user identity, durable checkpointer and
signed approval still require integration.

## Runtime replacement (this checkpoint)

`app/ai/agents/agent_async.py` now calls
`app/ai/execution/langgraph_orchestrator.py` rather than hand-rolling
the asynchronous multi-step loop. The StateGraph node + conditional-edge
structure is adapted from the upstream template's
`app/core/langgraph/graph.py` at the revision above. The integrated graph
retains Sankalpa's existing registry, authenticated executor, approval checks,
result contract and user-scoped run persistence.

The entire upstream `graph.py` is **not** vendored verbatim: it imports the
template's own OpenAI/mem0/prompt and PostgreSQL-auth code. Overwriting
Sankalpa's backend with those imports would break its Gemini/Groq policy and
user-isolation controls. Third-party code is imported under its license and
adapted only at existing integration boundaries.

Updates in the fifth branch: synchronous `execute_agent`, Gemini function calls
and Groq fallback function calls now also run through LangGraph StateGraphs.
The existing `/api/agent` heuristic planner remains for stable legacy command
contracts; an **opt-in, read-only, authenticated** Gemini fallback is available
via `INDOONE_AGENT_MODEL_PLANNER=true`. This is not a total planner replacement.
The upstream `ask_human` reference remains present for attribution; the opt-in
single-write approval flow now uses official `interrupt()` and `Command(resume=...)`
with official SQLite/PostgreSQL checkpoint implementations. The pause is a pure
review graph; the authenticated existing executor still gates external writes,
with a one-time database claim to prevent replay after crashes. Multi-step
resume and production verification remain pending.

See `docs/architecture/open_source_replacement_release_gate.md` for the
remaining external dependencies, 2 GiB release blockers and exact CI status.
**Do not merge while any required release gate is open.**

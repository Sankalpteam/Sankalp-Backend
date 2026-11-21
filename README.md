# Sankalp Backend

The Sankalp backend powers conversation, real-time research, authenticated
connectors, and verified tool execution for Web, Android, and supported channels.

## Production AI architecture

- **Gemini** is the primary model for chat, supported multimodal messages, and
  model-selected function calls.
- **Groq** is the secondary model for text and registered tool calls when Gemini
  encounters supported provider-busy failures.
- The **backend** executes tools. Models cannot bypass account credentials,
  tool registration, access checks, approval gates, or write confirmation.
- Connector results are evidence, not executable instructions. A failed or
  unavailable tool must never be reported as a completed action.
- There is **no local model checkpoint, CPU/GPU training, Colab or Azure OpenAI**
  in the production runtime.

### Configuration

Set secrets in the deployment environment, not in Git:

```dotenv
INDOONE_MODEL_BACKEND=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemma-4-26b-a4b-it
GEMINI_TOOL_MODEL=gemma-4-26b-a4b-it
GROQ_API_KEY=...
GROQ_MODEL=openai/gpt-oss-120b
```

The Gemini key is required for readiness. Groq is an optional configured
fallback. An unsupported old value such as \`local\` or \`azure\` intentionally
fails readiness instead of silently choosing another backend.

## Tool architecture

\`app/ai/execution/tool_registry.py\` is the authoritative tool catalog.
\`app/ai/execution/tool_loop.py\` discovers the tools appropriate to a user
turn, and dispatches execution through authenticated backend code.
\`app/ai/gateway/groq_tool_service.py\` converts declarations to Groq's
function-calling format and uses the **same executor**.

To add a new tool:

1. Register its name, input schema, description, and applicable permissions.
2. Implement a server-side executor; do not put API credentials in the client.
3. Require explicit confirmation and idempotency for write operations.
4. Add tests for normal execution, disconnected accounts, denial, error,
   provider fallback, and unsupported languages.
5. Verify both \`/api/chat\` and \`/api/chat/stream\` before release.

The existing GitHub, Gmail, Zoho, web research, calendar, document, and
channel integrations are independent of the retired training system.

## API and operational checks

- \`POST /api/chat\`: full conversation and registered tool routing.
- \`POST /api/chat/stream\`: streamed Gemini response, with safe one-chunk fallback.
- \`GET /ready\`: configuration readiness; returns 503 for missing Gemini setup.
- \`GET /health/details\`: safe deployment status (never API secrets).
- \`GET /api/observability\`: restricted model/tool counters when configured.

### Duplicate-action safety

Web and Android generate a fresh \`client_turn_id\` per user action, carried by
the blocking and streaming chat endpoints. SQLite/Postgres atomically claims
the ID using a **per-user unique key** before a tool executes.

- The same ID returns HTTP 409 rather than replaying the request.
- \`GET /api/chat/turns/{client_turn_id}\` lets an authenticated user inspect
  \`received\` / \`replied\` status; this is not proof of a provider write.
- A failed network connection may leave a record at \`received\`; the client
  must inspect the conversation and connected app before a new action.
- Request fingerprints are SHA-256 hashes rather than raw prompt text.
- Missing \`client_turn_id\` on legacy clients remains supported, but lacks
  this cross-request guard. Update both apps together with the backend.

This is **durable turn admission, not exactly-once execution** across
third-party providers. The existing approval and connector auth checks remain
mandatory, and a user choosing a new turn ID can make a new request.

## Development

```bash
python -m pip install -r requirements.txt
python -m compileall -q app tests scripts
python -m pytest -q -W error
python scripts/ops/security_smoke.py
```

The CI workflow also runs Postgres-backed store tests. A passing local unit
suite is **not** equivalent to live Gemini/Groq, OAuth, and deployment tests.

See \`deploy/DEPLOY.md\`, \`docs/BACKEND_CAPABILITIES.md\`, and \`.env.example\`
for deployment, connector and configuration details. Never push secrets or
user data into the repository.

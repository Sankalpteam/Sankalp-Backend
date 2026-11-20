# Sankalpa developer API / token-billing release gate

**Status (2026-11-20): development only. Do not merge, deploy, sell keys,
activate payments, or advertise active model tiers.**

## Purpose

Keep consumer Web/Android chat identities and their Gmail, Zoho, Instagram,
GitHub and other OAuth connections strictly separate from future external
B2B developer API keys. The customer pays for opted-in model API usage, **not**
for unrestricted access to other users' tool credentials.

The only AWS host currently allocated is the existing 2 GiB RAM production
server. This branch must not start a second service or modify that host.

**2026-11-20 development check:** CI validated Python tests, SQLite migrations,
PostgreSQL store/migration checks, and the frontend web/Android builds. CI is
not proof of live merchant settlement, provider resale permission, production
load capacity, or successful real-world provider billing.

## Implemented in the existing development branches

- Backend Alembic revision `0005_developer_api_keys`: owner-scoped key
  metadata and separately indexed provider-verified token-evidence table.
- New revision `0006_prepaid_reserve`: owner-scoped integer-paise prepaid balances
  and transactional per-request holds; no publicly callable balance crediting.
- `app/api/developer_prepaid.py`: internal-only reservation, duplicate-request
  protection, settlement using verified token counts, and safe pre-dispatch
  releases. Reservations retain their holds if provider outcome is ambiguous.
- `app/api/developer_provider_metering.py`: strict Gemini and Groq final-response
  usage metadata validation, fail-closed on missing/contradictory token evidence,
  model fallback mismatch and currently unpriced thought/cache/tool usage.
- `GET /api/developers/wallet`: owner-scoped read-only readiness snapshot,
  with paid inference and funding explicitly disabled.
- `app/api/developer_inference.py`: isolated `POST /v1/chat/completions`
  implementation is now present in development source, uses developer keys,
  idempotency fingerprints, pre-dispatch holds, per-key minute/concurrency
  limits, exact upstream token evidence and atomic settlement. It is
  **default-off** under independent resale, billing and gateway approval flags.
  It has no access to consumer chat OAuth tokens or tool credentials.
- `app/api/developer_keys.py`: Firebase-authenticated, owner-only
  `GET /api/developers/status`, `GET/POST /api/developers/keys`,
  `DELETE /api/developers/keys/{id}`, and
  `GET /api/developers/usage`.
- API key material is cryptographically random, HMAC-hashed with a distinct
  configured secret and returned once. Key lists return suffixes only.
  Cross-user revocation returns 404. Revoked keys fail internal resolution.
  The `INDOONE_DEVELOPER_KEYS_ENABLED` flag defaults to **false**.
- `record_verified_usage` is an **internal** function, not an endpoint:
  it accepts only explicitly provider-reported nonnegative token counts and
  uniquely indexed request IDs. Repeated request IDs are not double-counted.
  This is evidence storage, not a billable provider gateway.
- Frontend feature branch: Developer Keys and Usage pages use Firebase bearer
  tokens and actual backend records rather than hard-coded mock keys, rupees
  or fabricated token counts. Wallet top-up is disabled.

## NOT implemented / blockers before monetization

1. **Public inference release.** `/v1/chat/completions` now exists in the
   feature branch for isolated HTTP-contract testing, but is NOT enabled or
   approved for production. Its four independent feature/approval flags
   default to false. Confirm approved provider aliases, production provider
   responses, finance controls and legal permission before any activation.
   Developer keys must never work as Firebase credentials on `/api/chat`,
   `/api/agent` or connector endpoints.
2. **Real provider metering integration.** The new gated HTTP route binds
   upstream provider responses to reserved requests, matching model/version,
   response ID and exact usage metadata. These are mocked in CI, **not live
   provider validated**. The provider's pricing of reasoning, tool calls and
   cached input has not been approved. No billing is live. Never estimate
   tokens from text length or bill unverified fallback output.
3. **Budget reconciliation and operator controls.** Internal holds, integer-
   paise capture, durable dispatch markers, per-key rate/concurrency limits,
   response-ID uniqueness and idempotency are implemented and wired to the
   **disabled** HTTP route. Safe cancellation before dispatch exists, but
   post-dispatch unknown-outcome reconciliation, expired hold resolution,
   merchant funding, dispute workflow, and financial ledger sign-off are still
   missing. Do not silently release holds after ambiguous upstream timeouts.
4. **Approved model aliases, costs and INR pricing.** The frontend's
   `sankalp-mini/lite/standard/pro/max` labels and rupee rates are
   placeholders, NOT five functioning independent models. Fix exact
   upstream model/limits/currency, margin, tax and provider resale terms.
5. **Verified payments and receipts.** Real checkout/top-up, signed
   idempotent payment webhooks, refund/dispute handling, invoice/tax
   records, balance ledger reconciliation and customer support are missing.
   Do not invent successful payment results or fake balances.
6. **Security gates.** Rate limit and revoke by real key identity; ensure
   single-use developer key display, encryption/backup and key rotation,
   per-tenant project/usage isolation, audit logs, no secrets in output.
   The developer console must never trust localStorage UID as authentication.
7. **Real E2E.** Two distinct users with credentials, one live safe
   inference and authoritative provider token metadata, a billing
   reconciliation test, concurrent key revocation/race, webhook replay,
   2 GiB host load, Web/Android recovery and a staged rollback.
8. **Product and external provider permissions.** Verify current Gemini,
   Groq and payment processor terms before offering resale or claiming
   access to their models under Sankalpa branding.

## Environment switches

`INDOONE_DEVELOPER_KEYS_ENABLED=false` by default; management requires a
unique `INDOONE_DEVELOPER_KEY_HASH_SECRET` of at least 32 bytes.
The separate flags `INDOONE_B2B_GATEWAY_ENABLED`,
`INDOONE_B2B_RESALE_APPROVED`, and `INDOONE_B2B_BILLING_APPROVED` all
start false. `INDOONE_B2B_APPROVED_MODEL_CATALOG_JSON={}` has no saleable
models or prices. Never set the approval flags until external permissions,
merchant identity, pricing and live E2E have passed and been authorized.
**No production env values were changed by this development work.**

## Verification boundary

CI unit, integration and PostgreSQL jobs can verify code paths and schema.
They cannot verify live OAuth consent, the actual provider-reported tokens,
the merchant account, real customer money, or AWS memory without access and
explicit release authorization. Do not convert CI green into a claim that
the platform can sell API keys today.

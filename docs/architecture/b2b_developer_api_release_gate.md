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

## Implemented in the existing development branches

- Backend Alembic revision `0005_developer_api_keys`: owner-scoped key
  metadata and separately indexed provider-verified token-evidence table.
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

1. **Billable public inference route.** `/v1/chat/completions` is NOT
   implemented or enabled. Keys must never be accepted as Firebase user
   credentials on `/api/chat`, `/api/agent` or connector endpoints.
2. **Real provider metering.** Connect request-scoped Gemini/Groq usage
   metadata directly to the immutable evidence ledger. Never estimate
   saleable tokens as characters divided by four. Fail closed when provider
   usage metadata is missing, late, ambiguous or from a fallback provider.
3. **Budget reservation before provider dispatch.** Implement transactional
   prepaid balance/credit reservations, model token ceilings, per-key rate
   limits and atomic reconciliation. An already-sent request must not
   create a double charge during client retry or provider timeout.
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

`INDOONE_DEVELOPER_KEYS_ENABLED=false` by default; enabling management
requires a distinct `INDOONE_DEVELOPER_KEY_HASH_SECRET` of at least 32
bytes. This does not enable spendable/billable API endpoints. Production
key management and paid inference remain OFF until separately approved.

## Verification boundary

CI unit, integration and PostgreSQL jobs can verify code paths and schema.
They cannot verify live OAuth consent, the actual provider-reported tokens,
the merchant account, real customer money, or AWS memory without access and
explicit release authorization. Do not convert CI green into a claim that
the platform can sell API keys today.

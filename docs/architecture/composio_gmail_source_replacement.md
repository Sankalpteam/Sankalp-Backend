# Composio Gmail: official SDK adapter (opt-in, no extra AWS service)

Status: development feature branch only. Not deployed or connected to a real
Gmail user. Existing Gmail, Zoho and GitHub integrations remain unchanged.

Official upstream: https://github.com/ComposioHQ/composio (MIT)
SDK contract: Composio.sessions.create(), ToolRouterSession.execute(),
and https://docs.composio.dev/docs/authentication and
https://github.com/ComposioHQ/composio/blob/next/python/README.md

## What is installed

- A pure optional adapter in app/ai/execution/composio_bridge.py. Its client
  is the actual official Composio Python SDK, not a rewritten OAuth client.
- Only GMAIL_FETCH_EMAILS is permitted. User arguments cannot select a tool
  slug, another Composio user, email sender, or an arbitrary Gmail mailbox.
- The SDK uses a stable hashed Sankalpa authenticated principal for separate
  Composio sessions. Result type must be exactly completed.
- No credentials or user data are logged. A failed result is never treated as
  verified mailbox evidence. Each call bounds results and uses max two workers.
- Authenticated POST /api/composio/gmail/connect offers hosted consent link.
  This is independent of existing native OAuth user tokens.

## Single-host opt-in

The existing AWS server is ONE 2 GiB box already serving live production.
Do not start another container, daemon, MCP host or parallel feature checkout.

Install the optional SDK only at an approved cutover after memory measurement:
  .venv/bin/pip install -r requirements-composio.txt

Set private server variables after approval:
  INDOONE_COMPOSIO_GMAIL_READ_ENABLED=true
  COMPOSIO_API_KEY=<project key, NEVER committed>

Then each signed-in user must explicitly authorize Gmail via the new consent
route; native Gmail OAuth tokens cannot silently be transferred to Composio.

## Safety and remaining prerequisites

- Composio is a hosted OAuth provider. A user connecting Gmail consents to
  Composio's service and data handling. It is not fully self-hosted on AWS.
- Existing Gmail works without this SDK. No automatic overwrite or migration.
- Tests use mocked SDK instances; actual live Composio credentials/account
  onboarding and billing/quotas must be tested separately.
- SDK installation and its real process RSS must be measured before the feature
  can be enabled on the ONE production host.
- No Composio write tools, arbitrary MCP remote tools or autonomous OAuth
  handoff are permitted in this checkpoint.

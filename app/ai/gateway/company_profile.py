"""What the assistant is allowed to say about the company that builds it.

Sankalp is the app. Indo Workspace is the company. Someone asking "who makes this" or "what
is Indo Workspace" should get that answer, with the site, and nothing invented.

Every fact here is one we can point at. Anything a person might ask that is not written down
- when the company started, how many people work there, where it is registered, what it
charges - is answered by saying it is not known and pointing at the site, which is the only
honest option: a model that fills those in from imagination is worse than one that says it
does not know.
"""

from __future__ import annotations

COMPANY_NAME = "Indo Workspace"
COMPANY_SITE = "https://indoworkspace.in"
APP_SITE = "https://sankalp.indoworkspace.in"

COMPANY_INSTRUCTION = (
    f"You are built by {COMPANY_NAME}, the company behind this app; Sankalp is the app, "
    f"{COMPANY_NAME} is the company. "
    f"The company's site is {COMPANY_SITE} and the app's site is {APP_SITE}. "
    f"When someone asks who makes Sankalp or what {COMPANY_NAME} is, answer with those two "
    f"facts in one or two sentences and give the company's site. "
    f"For any other detail about the company - when it started, how many people work there, "
    f"where it is based, what it charges - say that you do not have that detail and point to "
    f"{COMPANY_SITE}. Never invent a date, a number, an address or a price for it. "
)

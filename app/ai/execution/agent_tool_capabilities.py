"""Canonical *policy* view of agent tools across local, connector and MCP paths.

A tool being advertised by an LLM, plugin, connector, or MCP server is NOT
proof that it is safe or even executable. The existing registered executor is
authoritative. This policy is intentionally separate from provider discovery:
future official MCP adapters must be explicitly registered with trusted server
configuration, owner-bound authorization and tested dispatch semantics.

The current opt-in Chat → LangGraph bridge is one consumer of this policy.
Existing legacy Gmail/Zoho, Composio Gmail, search and voice paths are
catalogued here as NOT YET MIGRATED, not falsely represented as runnable by
the graph. No dependency, process, or external connection is created.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from app.ai.execution.tool_registry import (
    EMAIL_TOOL_DECLARATIONS,
    SEARCH_WEB_DECLARATION,
    TOOL_SPECS,
    get_tool_spec,
)
from app.ai.execution.tools import TOOLS

Route = Literal["read", "review_write", "blocked", "legacy_only"]
Source = Literal["local", "connector", "legacy_email", "composio", "web_research"]


@dataclass(frozen=True)
class AgentToolCapability:
    name: str
    source: Source
    route: Route
    risk: str
    reason: str


def classify_registered_tool(
    name: str, *, allow_approval_write: bool = False
) -> AgentToolCapability:
    """Always fail closed for untrusted MCP names, undeclared executors or risk.

    This is a MODE admission decision, not connector authorization: the
    already-present run_tool_async dispatcher checks the authenticated user's
    connector capability and token scope before making a provider request.
    """
    normalized = str(name or "").strip().lower()
    spec = get_tool_spec(normalized)
    if spec is None or normalized not in TOOLS:
        return AgentToolCapability(normalized, "local", "blocked", "unknown",
                                   "tool is not in both trusted registry and executor")
    source: Source = "connector" if spec.connector_id else "local"
    if spec.connector_id and not spec.capability:
        return AgentToolCapability(normalized, source, "blocked", spec.risk,
                                   "connector capability metadata is incomplete")
    if spec.risk == "read" and not spec.requires_approval:
        return AgentToolCapability(normalized, source, "read", spec.risk,
                                   "read allowed; actual connector access checked at dispatch")
    if spec.risk == "write" and spec.requires_approval:
        if allow_approval_write:
            return AgentToolCapability(normalized, source, "review_write", spec.risk,
                                       "single write may pause for official LangGraph approval")
        return AgentToolCapability(normalized, source, "blocked", spec.risk,
                                   "durable official human approval feature is disabled")
    return AgentToolCapability(normalized, source, "blocked", spec.risk,
                               "risk is not covered by the current graph approval policy")


def admit_chat_plan(
    tool_names: Sequence[str], *, allow_approval_write: bool = False
) -> bool:
    """An all-READ plan, or one isolated write to be PAUSED; never a mixed plan."""
    if not tool_names:
        return True
    routes = [
        classify_registered_tool(name, allow_approval_write=allow_approval_write).route
        for name in tool_names
    ]
    return all(route == "read" for route in routes) or (
        len(routes) == 1 and routes[0] == "review_write"
    )


def coverage_inventory(*, approval_enabled: bool = False) -> tuple[AgentToolCapability, ...]:
    """Policy/reporting inventory, not automatic tool registration or a permission grant."""
    known = tuple(
        classify_registered_tool(item.name, allow_approval_write=approval_enabled)
        for item in TOOL_SPECS
    )
    # These are real *existing* tool-loop adapters, not the payload-only
    # registered LangGraph executor. Preserve their working legacy flows;
    # explicit official adapter+authorization tests are needed before migration.
    email = tuple(
        AgentToolCapability(str(item["name"]), "legacy_email", "legacy_only", "unclassified",
                            "Zoho/Gmail execution uses separate authenticated mail confirmation")
        for item in EMAIL_TOOL_DECLARATIONS
    )
    extras = (
        AgentToolCapability(
            "composio_gmail_fetch_emails", "composio", "legacy_only", "read",
            "opt-in Composio/MCP Gmail READ adapter is not a registered graph executor",
        ),
        AgentToolCapability(
            str(SEARCH_WEB_DECLARATION["name"]), "web_research", "legacy_only", "read",
            "public research runs through the existing tool loop",
        ),
    )
    return known + email + extras

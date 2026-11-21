"""Cross-source capability contract for every opt-in LangGraph tool route.

A plugin or remote MCP tool is not intrinsically authorized to run. This
covers ALL native registered tools, including local non-plugin helpers,
connected services and separately implemented legacy mail/MCP paths.
"""
from __future__ import annotations

import pytest

from app.ai.execution.agent_tool_capabilities import (
    admit_chat_plan, classify_registered_tool, coverage_inventory,
)
from app.ai.execution.tool_registry import EMAIL_TOOL_DECLARATIONS, TOOL_SPECS
from app.ai.execution.tools import TOOLS


def test_inventory_covers_every_trusted_executor_plus_out_of_graph_adapters():
    inventory = coverage_inventory()
    native = {item.name: item for item in inventory if item.source in ("local", "connector")}
    assert set(native) == {item.name for item in TOOL_SPECS}
    assert set(native) == set(TOOLS)
    assert any(x.source == "local" and x.route == "read" for x in native.values())
    assert any(x.source == "connector" and x.route == "read" for x in native.values())
    assert any(x.source == "connector" and x.route == "blocked" for x in native.values())
    legacy = {item.name: item for item in inventory if item.route == "legacy_only"}
    assert {x["name"] for x in EMAIL_TOOL_DECLARATIONS} <= set(legacy)
    assert "composio_gmail_fetch_emails" in legacy
    assert "search_web" in legacy


@pytest.mark.parametrize("name", [
    "calculator", "text_stats", "json_summary", "data_analysis",
    "task_schedule_build", "github_list_repositories", "github_list_branches",
    "instagram_get_profile",
])
def test_builtin_and_native_connected_reads_are_supported_without_plugin_special_case(name):
    capability = classify_registered_tool(name)
    assert capability.route == "read"
    assert capability.risk == "read"
    assert admit_chat_plan([name])


@pytest.mark.parametrize("name", [
    "github_delete_branch", "github_merge_pull_request",
])
def test_write_is_only_possible_as_one_paused_action_when_flag_on(name):
    assert classify_registered_tool(name).route == "blocked"
    assert classify_registered_tool(name, allow_approval_write=True).route == "review_write"
    assert not admit_chat_plan([name])
    assert admit_chat_plan([name], allow_approval_write=True)
    assert not admit_chat_plan(["text_stats", name], allow_approval_write=True)
    assert not admit_chat_plan([name, "text_stats"], allow_approval_write=True)
    assert not admit_chat_plan([name, name], allow_approval_write=True)


@pytest.mark.parametrize("name", [
    "github_create_branch", "github_create_pull_request",
    "github_update_repository_file", "sandbox_execution",
    "prepare_email_to_send", "create_email_draft",
    "composio_gmail_fetch_emails", "search_web",
    "mcp:untrusted.example/execute", "__import__('os')", "",
])
def test_unguarded_writes_sandboxes_and_unregistered_mcp_never_auto_route(name):
    assert classify_registered_tool(name, allow_approval_write=True).route == "blocked"
    assert not admit_chat_plan([name], allow_approval_write=True)


def test_every_registry_tool_has_exactly_one_fail_closed_route():
    inventory = coverage_inventory(approval_enabled=True)
    assert len({item.name for item in inventory}) == len(inventory)
    for item in inventory:
        assert item.route in {"read", "review_write", "blocked", "legacy_only"}
        assert item.reason
        if item.route == "read":
            assert item.source in ("local", "connector")
            assert item.risk == "read"
        if item.route == "review_write":
            assert item.source in ("local", "connector")
            assert item.risk == "write"
        if item.route == "legacy_only":
            assert item.source in ("legacy_email", "composio", "web_research")
            assert not admit_chat_plan([item.name], allow_approval_write=True)


def test_read_only_multi_step_plan_and_empty_plan_are_allowed():
    assert admit_chat_plan([])
    assert admit_chat_plan(["calculator", "text_stats", "github_list_branches"])
    assert not admit_chat_plan(["calculator", "sandbox_execution"])



def test_public_web_read_is_separate_operator_opt_in_only():
    assert classify_registered_tool("search_web").route == "blocked"
    assert not admit_chat_plan(["search_web"])
    policy = classify_registered_tool("search_web", allow_web_research=True)
    assert policy.source == "web_research"
    assert policy.risk == "read"
    assert policy.route == "read"
    assert admit_chat_plan(["search_web"], allow_web_research=True)
    assert not admit_chat_plan(
        ["search_web", "github_delete_branch"],
        allow_web_research=True, allow_approval_write=True,
    )
    assert not admit_chat_plan(["mcp:unreviewed/search"], allow_web_research=True)
    assert not admit_chat_plan(["prepare_email_to_send"], allow_web_research=True)


def test_zoho_mail_graph_route_is_one_trusted_read_only_and_default_blocked():
    assert classify_registered_tool("list_recent_emails").route == "blocked"
    assert not admit_chat_plan(["list_recent_emails"])
    capability = classify_registered_tool("list_recent_emails", allow_zoho_read=True)
    assert (capability.source, capability.risk, capability.route) == (
        "legacy_email", "read", "read",
    )
    assert admit_chat_plan(["list_recent_emails"], allow_zoho_read=True)
    for name in (
        "search_emails", "read_email", "create_email_draft", "prepare_email_reply",
        "prepare_email_to_send", "prepare_email_deletion", "mcp:untrusted/mail",
    ):
        assert not admit_chat_plan([name], allow_zoho_read=True)
    assert not admit_chat_plan(
        ["list_recent_emails", "github_delete_branch"],
        allow_zoho_read=True, allow_approval_write=True,
    )

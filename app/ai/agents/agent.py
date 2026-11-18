from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import re
import time
from typing import Any, Iterable

from app.ai.execution.agent_contracts import serialize_tool_result
from app.ai.execution.agent_policy import deadline_failure, remaining_budget, should_retry
from app.ai.execution.approval import decide_tool
from app.ai.execution.coding import build_code_tool_payload
from app.ai.agents.orchestrator import Plan, plan_request
from app.ai.execution.tool_registry import get_tool_spec, tool_names
from app.ai.execution.tools import ToolResult, run_tool
from app.capabilities.connectors.store import create_agent_run, search_memories, update_agent_run

MAX_AGENT_STEPS = 4
MAX_AGENT_MEMORIES = 5
MAX_AGENT_RETRIES = 1
MAX_AGENT_MESSAGE_LENGTH = 20_000
MAX_AGENT_PAYLOAD_LENGTH = 8_000
MAX_AGENT_RUNTIME_SECONDS = 15.0
MAX_AGENT_APPROVAL_TOKENS = 16
ALLOWED_AGENT_TOOLS = tool_names()
_CHAIN_REFERENCE_PATTERN = re.compile(r"(?<!\w)\$(?:last|result\d+)(?!\w)")
# The coding executors take a JSON {language, code} payload, not a bare string.
_CODING_TOOLS = frozenset({"sandbox_execution", "code_transform", "code_fix_suggestions", "code_analysis"})

@dataclass(frozen=True)
class AgentStep:
    index: int
    tool: str
    payload: str
    requires_approval: bool = False

@dataclass(frozen=True)
class AgentExecution:
    message: str
    steps: tuple[AgentStep, ...]
    results: tuple[ToolResult, ...]
    memories: tuple[dict[str, Any], ...] = ()
    blocked_steps: tuple[AgentStep, ...] = ()
    retry_counts: tuple[int, ...] = ()
    run_id: str | None = None

def _step(tool: str, payload: str, index: int) -> AgentStep:
    if len(payload) > MAX_AGENT_PAYLOAD_LENGTH:
        payload = payload[:MAX_AGENT_PAYLOAD_LENGTH]
    spec = get_tool_spec(tool)
    return AgentStep(index=index, tool=tool, payload=payload, requires_approval=True if spec is None else spec.requires_approval)

def _extract_explicit_requests(message: str) -> list[tuple[int, str, str]]:
    patterns = (("sandbox_execution", r"(?:run|execute|sandbox)\s+code\s*:\s*(.+)$"),("code_transform", r"(?:transform|normalize|format)\s+code\s*:\s*(.+)$"),("code_fix_suggestions", r"(?:fix suggestions|suggest fixes|debug code)\s*:\s*(.+)$"),("code_analysis", r"(?:code analysis|analyze code)\s*:\s*(.+)$"),("json_summary", r"(?:summarize|summary of)\s+json\s*:\s*(\{.*?\}|\[.*?\])"),("text_stats", r"(?:text stats|analyze text|count words)\s*:\s*(.+?)(?=\s*(?:;|$))"))
    matches: list[tuple[int, str, str]] = []
    for tool, pattern in patterns:
        for match in re.finditer(pattern, message, re.IGNORECASE | re.DOTALL):
            payload = match.group(1).strip()
            # The pattern captures bare code; the coding tools need {language, code}.
            if tool in _CODING_TOOLS:
                payload = build_code_tool_payload(payload)
            matches.append((match.start(), tool, payload))
    matches.sort(key=lambda item: item[0])
    return matches[:MAX_AGENT_STEPS]

def _extract_calculations(message: str) -> tuple[str, ...]:
    operand = r"(?:\$last|\$result\d+|\d+(?:\.\d+)?)"
    expression = rf"(?<!\w){operand}(?:\s*[+\-*/%]\s*{operand})+(?!\w)"
    matches = re.findall(expression, message)
    return tuple(match.replace(" ", "") for match in matches[:MAX_AGENT_STEPS])

def build_agent_steps(message: str, user_id: str = "", contacts: list[dict[str, Any]] | None = None) -> tuple[AgentStep, ...]:
    normalized = message.strip()
    if not normalized or len(normalized) > MAX_AGENT_MESSAGE_LENGTH: return ()
    explicit = _extract_explicit_requests(normalized)
    if explicit: return tuple(_step(tool, payload, index) for index, (_, tool, payload) in enumerate(explicit, start=1))
    expressions = _extract_calculations(normalized)
    if expressions: return tuple(_step("calculator", expression, index) for index, expression in enumerate(expressions, start=1))
    plan: Plan = plan_request(normalized)
    if not plan.executable: return ()
    return (_step(plan.tool or "", plan.tool_payload or "", 1),)

def _load_memory_context(user_id: str, message: str) -> tuple[dict[str, Any], ...]:
    if not user_id.strip(): return ()
    return tuple(search_memories(user_id, message[:MAX_AGENT_PAYLOAD_LENGTH], limit=MAX_AGENT_MEMORIES))

def _chain_payload(payload: str, results: tuple[ToolResult, ...]) -> str:
    payload = payload[:MAX_AGENT_PAYLOAD_LENGTH]
    if not results: return payload
    def replace_reference(match: re.Match[str]) -> str:
        reference = match.group(0)
        if reference == "$last": return results[-1].output
        index = int(reference.removeprefix("$result"))
        if 1 <= index <= len(results): return results[index - 1].output
        return reference
    chained = _CHAIN_REFERENCE_PATTERN.sub(replace_reference, payload)
    return chained[:MAX_AGENT_PAYLOAD_LENGTH]

def _unresolved_chain_references(payload: str, results: tuple[ToolResult, ...]) -> tuple[str, ...]:
    unresolved: set[str] = set()
    for reference in _CHAIN_REFERENCE_PATTERN.findall(payload):
        if reference == "$last":
            if not results: unresolved.add(reference)
            continue
        index = int(reference.removeprefix("$result"))
        if index < 1 or index > len(results): unresolved.add(reference)
    return tuple(sorted(unresolved, key=lambda value: (value != "$last", value)))

def _run_with_retry(tool: str, payload: str, deadline: float, user_id: str = "") -> tuple[ToolResult, int]:
    def invoke(remaining: float) -> ToolResult:
        parameters = inspect.signature(run_tool).parameters
        kwargs: dict[str, Any] = {}
        if "max_runtime_seconds" in parameters:
            kwargs["max_runtime_seconds"] = remaining
        if "user_id" in parameters:
            kwargs["user_id"] = user_id
        return run_tool(tool, payload, **kwargs)
    remaining = remaining_budget(deadline)
    if remaining <= 0: return deadline_failure(tool), 0
    result = invoke(remaining)
    retry_count = 0
    while should_retry(result, retry_count, MAX_AGENT_RETRIES, tool=tool):
        remaining = remaining_budget(deadline)
        if remaining <= 0: return deadline_failure(tool), retry_count
        retry_count += 1
        result = invoke(remaining)
    return result, retry_count

def _serialize_step(step: AgentStep) -> dict[str, Any]: return {"index": step.index, "tool": step.tool, "payload": step.payload, "requires_approval": step.requires_approval}
def _serialize_result(result: ToolResult) -> dict[str, Any]: return serialize_tool_result(result)

def _bounded_approval_tokens(approval_tokens: Iterable[str] | None) -> tuple[str, ...]:
    if approval_tokens is None: return ()
    iterator = iter(approval_tokens); tokens: list[str] = []
    for _ in range(MAX_AGENT_APPROVAL_TOKENS):
        try: tokens.append(next(iterator))
        except StopIteration: return tuple(tokens)
    return tuple(tokens)

def execute_agent(message: str, user_id: str = "", approved_tools: set[str] | frozenset[str] | None = None, contacts: list[dict[str, Any]] | None = None, approval_tokens: Iterable[str] | None = None) -> AgentExecution:
    normalized = message.strip()
    if not normalized or len(normalized) > MAX_AGENT_MESSAGE_LENGTH: return AgentExecution(message=message, steps=(), results=())
    approved = frozenset(approved_tools or ())
    approval_tokens_tuple = _bounded_approval_tokens(approval_tokens)
    memory_context = _load_memory_context(user_id, normalized)
    run_id: str | None = None
    if user_id.strip(): run_id = str(create_agent_run(user_id, normalized)["id"])
    planned_steps = build_agent_steps(normalized, user_id=user_id, contacts=contacts)[:MAX_AGENT_STEPS]
    # Use the same maintained LangGraph workflow engine as the async endpoint.
    # No new provider runtime or server is created for this synchronous caller.
    from app.ai.execution.langgraph_sync_orchestrator import run_graph_execution_sync

    planned_steps = planned_steps[:MAX_AGENT_STEPS]
    deadline = time.monotonic() + MAX_AGENT_RUNTIME_SECONDS
    graph_result = run_graph_execution_sync(
        planned_steps,
        user_id=user_id,
        approved_tools=approved,
        approval_tokens=approval_tokens_tuple,
        deadline=deadline,
        execute_with_retry=_run_with_retry,
    )
    executable = graph_result["steps"]
    blocked = graph_result["blocked_steps"]
    results = graph_result["results"]
    retry_counts = graph_result["retry_counts"]
    unsafe_result = any(not result.safe for result in results)
    timed_out = bool(blocked and len(executable) + len(blocked) >= len(planned_steps) and remaining_budget(deadline) <= 0)
    status = "failed" if unsafe_result else ("blocked" if blocked and not results else ("timed_out" if timed_out else "completed"))
    execution = AgentExecution(message=normalized, steps=executable, results=results, memories=memory_context, blocked_steps=blocked, retry_counts=retry_counts, run_id=run_id)
    if user_id.strip() and run_id is not None:
        update_agent_run(user_id=user_id, run_id=run_id, status=status, steps=[_serialize_step(step) for step in execution.steps], results=[_serialize_result(result) for result in execution.results], blocked_steps=[_serialize_step(step) for step in execution.blocked_steps], retry_counts=list(execution.retry_counts))
    return execution

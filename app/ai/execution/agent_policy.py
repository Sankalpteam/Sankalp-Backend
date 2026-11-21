from __future__ import annotations

import time

from app.ai.execution.tools import ToolResult
from app.ai.execution.tool_registry import get_tool_spec


def remaining_budget(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def should_retry(
    result: ToolResult,
    retry_count: int,
    max_retries: int,
    *,
    tool: str | None = None,
) -> bool:
    """Retry failed read-only tools, never an uncertain write.

    A failed write can already have reached GitHub, email or another provider.
    Automatic retries require durable idempotency, which is not provided by
    the legacy agent API. Unknown tools fail closed when named.
    """
    if retry_count >= max_retries or result.safe or not result.retryable:
        return False
    if tool is not None:
        spec = get_tool_spec(tool)
        if spec is None or spec.requires_approval or spec.risk != "read":
            return False
    return True


def deadline_failure(tool: str) -> ToolResult:
    return ToolResult(tool, "Tool execution deadline exceeded", safe=False, retryable=False)

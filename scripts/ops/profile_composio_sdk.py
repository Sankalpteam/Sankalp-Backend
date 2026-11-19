"""GitHub-runner-only import and interface smoke for official Composio SDK.

No API key, customer data, cloud request or AWS deployment. This verifies the
optional real package is importable and exposes the session API assumed by our
Composio bridge. ru_maxrss is an upper bound for THIS isolated CI process, not
free RAM on the existing production 2 GiB host.
"""
from __future__ import annotations

import importlib.metadata
import json
import resource
import time


def main() -> None:
    baseline = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    start = time.perf_counter()
    from composio import Composio
    from composio.core.models.tool_router_session import ToolRouterSession

    sdk = Composio(api_key="ci-interface-smoke-not-a-real-key", timeout=5, max_retries=0)
    assert callable(getattr(sdk.sessions, "create", None)), "SDK sessions.create() contract changed"
    assert callable(getattr(ToolRouterSession, "execute", None)), "SDK execute() contract changed"
    assert callable(getattr(ToolRouterSession, "authorize", None)), "SDK authorize() contract changed"
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    print(json.dumps({
        "label": "official Composio SDK import on GitHub runner; NOT AWS production RAM",
        "composio_version": importlib.metadata.version("composio"),
        "baseline_rss_kib": baseline,
        "import_max_rss_kib": peak,
        "import_delta_upper_bound_kib": max(0, peak - baseline),
        "elapsed_seconds": round(time.perf_counter() - start, 3),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

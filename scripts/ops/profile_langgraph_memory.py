"""GitHub-CI-only LangGraph process RSS smoke (NOT the live AWS host).

ru_maxrss is maximum resident set size measured by Linux in KiB. It includes
the Python runtime and imported SDKs, not the already-running production
Uvicorn process, database, OS and other services. Never infer 2 GiB fit from
this single measurement.
"""
from __future__ import annotations

import json
import resource
import time


def rss_kib() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def main() -> None:
    baseline = rss_kib()
    started = time.perf_counter()
    from langgraph.graph import END, StateGraph
    from typing import TypedDict

    class State(TypedDict):
        value: int

    builder = StateGraph(State)
    builder.add_node("done", lambda state: {"value": state["value"] + 1})
    builder.set_entry_point("done")
    builder.add_edge("done", END)
    app = builder.compile()
    assert app.invoke({"value": 1})["value"] == 2
    graph_rss = rss_kib()
    print(json.dumps({
        "label": "CI runner only; not production AWS memory",
        "baseline_rss_kib": baseline,
        "langgraph_import_and_invoke_max_rss_kib": graph_rss,
        "rss_increase_kib_upper_bound": max(0, graph_rss - baseline),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

"""Active runtime supervisors for security, connectors, and operations.

These are guarded backend services, not competing language-model planners.
Future plugins register through app.ai.execution.tool_registry.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_DIRECTORY = Path(__file__).resolve().parent
_cache: dict[str, ModuleType] = {}


def load(name: str) -> ModuleType:
    if name in _cache:
        return _cache[name]
    path = _DIRECTORY / f"{name}.py"
    if not path.is_file():
        raise ImportError(f"unknown runtime supervisor: {name}")
    spec = importlib.util.spec_from_file_location(f"app.agents.{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load runtime supervisor: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    _cache[name] = module
    return module


def gatekeeper() -> ModuleType:
    return load("01_citadel_gatekeeper")


def sentinel() -> ModuleType:
    return load("02_inquisitor_sentinel")


def vault() -> ModuleType:
    return load("03_vault_guardian")


def marshal() -> ModuleType:
    return load("04_indic_polyglot_marshal")


def intent() -> ModuleType:
    return load("05_intent_purity_extractor")


def treasury() -> ModuleType:
    return load("07_treasury_cache")


def condenser() -> ModuleType:
    return load("08_context_condenser")


def arbiter() -> ModuleType:
    return load("09_token_budget_arbiter")


def model_arbiter() -> ModuleType:
    return load("10_model_arbiter")


def github_supervisor() -> ModuleType:
    return load("11_github_supervisor")


def mail_supervisor() -> ModuleType:
    return load("12_zoho_mail_supervisor")


def web_supervisor() -> ModuleType:
    return load("14_web_search_supervisor")


def code_sandbox() -> ModuleType:
    return load("15_code_sandbox_runner")


def quota_guard() -> ModuleType:
    return load("22_omnichannel_quota_guard")


def kingdom_auditor() -> ModuleType:
    return load("27_master_kingdom_health_auditor")


def capability_gate() -> ModuleType:
    return load("capability_gate")



from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def check_deployment_profile(profile: str) -> dict[str, Any]:
    normalized_profile = profile or "dev"
    checks = [_local_data_root_check()]
    if normalized_profile == "secure-offline":
        checks.append(_network_llm_disabled_check())
    elif normalized_profile == "lab-local":
        checks.append(_network_llm_optional_check())
    else:
        normalized_profile = "dev"
        checks.append(_dev_profile_check())
    return {
        "profile": normalized_profile,
        "ready": all(check["status"] == "passed" for check in checks),
        "checks": checks,
    }


def _local_data_root_check() -> dict[str, str]:
    root = Path(os.environ.get("NLP_SKILL_AGENTS_DATA_DIR", "local_data"))
    root.mkdir(parents=True, exist_ok=True)
    return {
        "id": "local_data_root",
        "status": "passed",
        "message": f"Using local data root: {root}",
    }


def _network_llm_disabled_check() -> dict[str, str]:
    if os.environ.get("OPENROUTER_API_KEY"):
        return {
            "id": "network_llm_disabled",
            "status": "failed",
            "message": "OPENROUTER_API_KEY is configured; disable it for secure-offline.",
        }
    return {
        "id": "network_llm_disabled",
        "status": "passed",
        "message": "No OpenRouter API key is configured.",
    }


def _network_llm_optional_check() -> dict[str, str]:
    return {
        "id": "network_llm_optional",
        "status": "passed",
        "message": "Network LLM authoring is optional in lab-local profile.",
    }


def _dev_profile_check() -> dict[str, str]:
    return {
        "id": "dev_profile",
        "status": "passed",
        "message": "Development profile allows local storage and optional external authoring.",
    }

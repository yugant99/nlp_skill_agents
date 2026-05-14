from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


DEFAULT_OPENROUTER_MODEL = "openai/gpt-oss-120b"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_DOTENV_LOADED = False


class OpenRouterError(RuntimeError):
    """Raised when optional OpenRouter authoring fails."""


@dataclass(frozen=True)
class OpenRouterConfig:
    api_key: str
    model: str = DEFAULT_OPENROUTER_MODEL
    site_url: str = "http://127.0.0.1:5173"
    app_name: str = "NLP Skill Agents"
    timeout_seconds: float = 45.0


def is_openrouter_configured() -> bool:
    _load_local_dotenv_once()
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def complete_json(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
) -> dict[str, Any]:
    config = _config_from_env(model)
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    response_payload = _post_chat_completion(payload, config)
    content = _message_content(response_payload)
    return _parse_json_content(content)


def _config_from_env(model: str | None = None) -> OpenRouterConfig:
    _load_local_dotenv_once()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is not configured")
    return OpenRouterConfig(
        api_key=api_key,
        model=model or os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL),
        site_url=os.environ.get("OPENROUTER_SITE_URL", "http://127.0.0.1:5173"),
        app_name=os.environ.get("OPENROUTER_APP_NAME", "NLP Skill Agents"),
    )


def _load_local_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    for path in _candidate_dotenv_paths():
        if path.exists():
            _load_dotenv_file(path)
    _DOTENV_LOADED = True


def _candidate_dotenv_paths() -> list:
    repo_root = Path(__file__).resolve().parents[2]
    cwd = Path.cwd()
    candidates = [cwd / ".env", repo_root / ".env"]
    unique = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def _load_dotenv_file(path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _post_chat_completion(
    payload: dict[str, Any],
    config: OpenRouterConfig,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": config.site_url,
        "X-Title": config.app_name,
    }
    with httpx.Client(timeout=config.timeout_seconds) as client:
        response = client.post(OPENROUTER_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def _message_content(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError("OpenRouter response did not include message content") from exc
    if not isinstance(content, str) or not content.strip():
        raise OpenRouterError("OpenRouter response content was empty")
    return content


def _parse_json_content(content: str) -> dict[str, Any]:
    candidates = [
        content.strip(),
        *_json_code_blocks(content),
        _first_json_object(content),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise OpenRouterError("OpenRouter response did not contain valid JSON")


def _json_code_blocks(content: str) -> list[str]:
    return re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", content, flags=re.DOTALL)


def _first_json_object(content: str) -> str:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return content[start : end + 1]

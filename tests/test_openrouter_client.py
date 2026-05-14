import os

import pytest

from backend.llm.openrouter import (
    OpenRouterConfig,
    OpenRouterError,
    complete_json,
    is_openrouter_configured,
)


def test_is_openrouter_configured_reads_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert is_openrouter_configured() is False

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    assert is_openrouter_configured() is True


def test_complete_json_sends_model_and_parses_json(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = []

    def fake_post(payload, config):
        calls.append((payload, config))
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"id":"demo","name":"Demo","version":"1.0.0"}'
                    }
                }
            ]
        }

    monkeypatch.setattr("backend.llm.openrouter._post_chat_completion", fake_post)

    result = complete_json(
        system_prompt="Return JSON.",
        user_prompt="Build a skill pack.",
        model="openai/gpt-oss-120b",
    )

    assert result == {"id": "demo", "name": "Demo", "version": "1.0.0"}
    payload, config = calls[0]
    assert isinstance(config, OpenRouterConfig)
    assert config.api_key == "test-key"
    assert payload["model"] == "openai/gpt-oss-120b"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"


def test_complete_json_raises_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(OpenRouterError, match="OPENROUTER_API_KEY"):
        complete_json(system_prompt="Return JSON.", user_prompt="Build a skill pack.")


def test_complete_json_extracts_fenced_json(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def fake_post(payload, config):
        return {
            "choices": [
                {
                    "message": {
                        "content": 'Here is the pack:\\n```json\\n{"id":"demo"}\\n```'
                    }
                }
            ]
        }

    monkeypatch.setattr("backend.llm.openrouter._post_chat_completion", fake_post)

    assert complete_json("system", "user") == {"id": "demo"}


def test_complete_json_rejects_invalid_json(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def fake_post(payload, config):
        return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr("backend.llm.openrouter._post_chat_completion", fake_post)

    with pytest.raises(OpenRouterError, match="valid JSON"):
        complete_json("system", "user")

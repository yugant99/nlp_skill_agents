from backend.storage.deployment_profiles import check_deployment_profile


def test_secure_offline_profile_requires_local_storage_and_disables_network_llm(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    result = check_deployment_profile("secure-offline")

    assert result["profile"] == "secure-offline"
    assert result["ready"] is True
    assert result["checks"] == [
        {
            "id": "local_data_root",
            "status": "passed",
            "message": f"Using local data root: {tmp_path}",
        },
        {
            "id": "network_llm_disabled",
            "status": "passed",
            "message": "No OpenRouter API key is configured.",
        },
    ]


def test_secure_offline_profile_fails_when_openrouter_key_is_configured(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("NLP_SKILL_AGENTS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    result = check_deployment_profile("secure-offline")

    assert result["ready"] is False
    assert result["checks"][1] == {
        "id": "network_llm_disabled",
        "status": "failed",
        "message": "OPENROUTER_API_KEY is configured; disable it for secure-offline.",
    }

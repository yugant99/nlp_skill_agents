from fastapi.testclient import TestClient

from backend.analysis.skill_builder import draft_skill_pack_from_brief, refine_skill_pack
from backend.analysis.skill_packs import parse_skill_pack
from backend.app.main import app


def test_draft_skill_pack_from_healthcare_brief_creates_valid_dynamic_pack() -> None:
    draft = draft_skill_pack_from_brief(
        "Caregiver participant healthcare study. Track pain, medication, "
        "walking and balance. Nonverbal cues include pause and laughter.",
        name="Caregiver Mobility Study",
    )

    pack = parse_skill_pack(draft.payload)

    assert pack.id == "caregiver_mobility_study"
    assert pack.name == "Caregiver Mobility Study"
    assert [metric.id for metric in pack.metrics] == [
        "base_metrics",
        "lexical_metrics",
        "disfluency_metrics",
        "concept_count_metrics",
        "cue_inventory_metrics",
    ]
    assert pack.speaker_prefixes["caregiver"] == [
        "CG",
        "Caregiver",
        "Interviewer",
    ]
    assert pack.speaker_prefixes["participant"] == [
        "P",
        "Participant",
        "Patient",
    ]
    assert set(pack.concept_lexicons) == {"pain", "medication", "mobility"}
    assert set(pack.nonverbal_cues) == {"pause", "laughter"}
    assert draft.warnings == []


def test_draft_skill_pack_falls_back_to_psychology_defaults_when_brief_is_broad() -> None:
    draft = draft_skill_pack_from_brief(
        "Psychology interview about mood, stress, social support, and coping.",
    )

    pack = parse_skill_pack(draft.payload)

    assert pack.name == "Psychology Interview Draft"
    assert "interviewer" in pack.speaker_prefixes
    assert set(pack.concept_lexicons) == {
        "mood",
        "stress",
        "social_support",
        "coping",
    }
    assert "pause" in pack.nonverbal_cues


def test_draft_skill_pack_endpoint_returns_payload_and_summary() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/skill-packs/draft",
        json={
            "brief": "Therapy conversation tracking emotion, risk, goals, and pauses.",
            "name": "Therapy Risk Review",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["payload"]["id"] == "therapy_risk_review"
    assert body["skill_pack"]["name"] == "Therapy Risk Review"
    assert body["skill_pack"]["metric_ids"] == [
        "base_metrics",
        "lexical_metrics",
        "disfluency_metrics",
        "concept_count_metrics",
        "cue_inventory_metrics",
    ]
    assert set(body["payload"]["concept_lexicons"]) == {"emotion", "risk", "goals"}


def test_refine_skill_pack_splits_pain_and_adds_sleep() -> None:
    draft = draft_skill_pack_from_brief(
        "Caregiver participant healthcare study. Track pain and medication.",
        name="Care Study",
    )

    refined = refine_skill_pack(
        draft.payload,
        "Split pain into acute and chronic pain, and add sleep disruption.",
    )
    pack = parse_skill_pack(refined.payload)

    assert "pain" not in pack.concept_lexicons
    assert pack.concept_lexicons["acute_pain"] == [
        "acute",
        "sharp",
        "sudden",
        "hurt",
        "hurts",
    ]
    assert pack.concept_lexicons["chronic_pain"] == [
        "chronic",
        "ongoing",
        "persistent",
        "ache",
        "aches",
        "sore",
    ]
    assert "sleep" in pack.concept_lexicons
    assert refined.applied_changes == [
        "split pain into acute_pain and chronic_pain",
        "added concept sleep",
    ]


def test_refine_skill_pack_endpoint_returns_updated_payload() -> None:
    client = TestClient(app)
    payload = draft_skill_pack_from_brief(
        "Psychology interview about mood and stress.",
        name="Interview Study",
    ).payload

    response = client.post(
        "/api/skill-packs/refine",
        json={
            "payload": payload,
            "instruction": "Add anxiety and remove stress.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "anxiety" in body["payload"]["concept_lexicons"]
    assert "stress" not in body["payload"]["concept_lexicons"]
    assert body["applied_changes"] == [
        "added concept anxiety",
        "removed concept stress",
    ]


def test_draft_skill_pack_endpoint_can_use_openrouter(monkeypatch) -> None:
    client = TestClient(app)
    captured = {}

    def fake_complete_json(system_prompt, user_prompt, model=None):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["model"] = model
        return {
            "id": "llm_care_study",
            "name": "LLM Care Study",
            "version": "0.1.0",
            "description": "Generated from brief.",
            "metrics": [
                "base_metrics",
                "lexical_metrics",
                "disfluency_metrics",
                "concept_count_metrics",
                "cue_inventory_metrics",
            ],
            "speaker_roles": {
                "caregiver": {"label": "Care Partner", "prefixes": ["CG"]},
                "participant": {"label": "Participant", "prefixes": ["P"]},
            },
            "disfluency_tokens": ["um", "uh"],
            "concept_lexicons": {"pain": ["pain", "hurts"]},
            "nonverbal_cues": {"pause": ["pause"]},
        }

    monkeypatch.setattr("backend.analysis.skill_builder.complete_json", fake_complete_json)

    response = client.post(
        "/api/skill-packs/draft",
        json={
            "brief": "Build a caregiver pain study.",
            "name": "LLM Care Study",
            "authoring_engine": "openrouter",
            "model": "openai/gpt-oss-120b",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["payload"]["id"] == "llm_care_study"
    assert body["authoring"] == {
        "engine": "openrouter",
        "model": "openai/gpt-oss-120b",
    }
    assert captured["model"] == "openai/gpt-oss-120b"
    assert "transcript content" in captured["system_prompt"].lower()


def test_openrouter_draft_normalizes_metric_object_to_metric_ids(monkeypatch) -> None:
    def fake_complete_json(system_prompt, user_prompt, model=None):
        return {
            "id": "c3f9a1e2-7b4d-4f2a-9d6e-1a2b3c4d5e6f",
            "name": "OpenRouter Demo Study",
            "version": "1.0.0",
            "description": "Generated from brief.",
            "metrics": {
                "base_metrics": {"note": "turns"},
                "concept_count_metrics": {"note": "concepts"},
                "not_registered": {"note": "ignore"},
            },
            "speaker_roles": {
                "caregiver": {"label": "Caregiver", "prefixes": ["CG"]},
                "participant": {"label": "Participant", "prefixes": ["P"]},
            },
            "disfluency_tokens": ["um"],
            "concept_lexicons": {"pain": ["pain"]},
            "nonverbal_cues": {"pause": ["pause"]},
        }

    monkeypatch.setattr("backend.analysis.skill_builder.complete_json", fake_complete_json)

    response = client = TestClient(app)
    result = client.post(
        "/api/skill-packs/draft",
        json={
            "brief": "Caregiver pain study.",
            "name": "OpenRouter Demo Study",
            "authoring_engine": "openrouter",
        },
    )

    assert result.status_code == 200
    body = result.json()
    assert body["payload"]["id"] == "openrouter_demo_study"
    assert body["payload"]["metrics"] == [
        "base_metrics",
        "concept_count_metrics",
    ]


def test_openrouter_draft_normalizes_invalid_speaker_roles(monkeypatch) -> None:
    def fake_complete_json(system_prompt, user_prompt, model=None):
        return {
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "description": "Generated from brief.",
            "metrics": ["concept_count_metrics"],
            "speaker_roles": ["caregiver", "participant"],
            "disfluency_tokens": ["um"],
            "concept_lexicons": {"pain": ["pain"]},
            "nonverbal_cues": {"pause": ["pause"]},
        }

    monkeypatch.setattr("backend.analysis.skill_builder.complete_json", fake_complete_json)

    response = TestClient(app).post(
        "/api/skill-packs/draft",
        json={
            "brief": "Caregiver participant pain study.",
            "name": "Demo",
            "authoring_engine": "openrouter",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["payload"]["speaker_roles"]["caregiver"]["prefixes"] == [
        "CG",
        "Caregiver",
        "Interviewer",
    ]


def test_refine_skill_pack_endpoint_can_use_openrouter(monkeypatch) -> None:
    client = TestClient(app)
    payload = draft_skill_pack_from_brief(
        "Caregiver participant healthcare study. Track pain.",
        name="Care Study",
    ).payload

    def fake_complete_json(system_prompt, user_prompt, model=None):
        updated = dict(payload)
        updated["concept_lexicons"] = {
            "acute_pain": ["acute", "sharp"],
            "chronic_pain": ["chronic", "ongoing"],
        }
        return {
            "payload": updated,
            "applied_changes": ["split pain into acute_pain and chronic_pain"],
            "warnings": [],
        }

    monkeypatch.setattr("backend.analysis.skill_builder.complete_json", fake_complete_json)

    response = client.post(
        "/api/skill-packs/refine",
        json={
            "payload": payload,
            "instruction": "Split pain into acute and chronic.",
            "authoring_engine": "openrouter",
            "model": "openai/gpt-oss-120b",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body["payload"]["concept_lexicons"]) == {
        "acute_pain",
        "chronic_pain",
    }
    assert body["applied_changes"] == [
        "split pain into acute_pain and chronic_pain"
    ]
    assert body["authoring"] == {
        "engine": "openrouter",
        "model": "openai/gpt-oss-120b",
    }

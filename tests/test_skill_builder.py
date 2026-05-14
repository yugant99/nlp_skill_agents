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

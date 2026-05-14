import json
from pathlib import Path

import pytest

from backend.analysis.skill_packs import SkillPackValidationError, load_skill_pack


def test_load_skill_pack_by_id_from_default_directory() -> None:
    pack = load_skill_pack("default_transcript_metrics")

    assert pack.id == "default_transcript_metrics"
    assert pack.name == "Default Transcript Metrics"
    assert pack.version == "0.1.0"
    assert [metric.id for metric in pack.metrics] == [
        "base_metrics",
        "lexical_metrics",
        "disfluency_metrics",
    ]
    assert pack.disfluency_tokens[:3] == ["hm", "huh", "um"]
    assert pack.raw["speaker_roles"]["caregiver"] == "Caregiver"


def test_builtin_dynamic_templates_load_with_research_definitions() -> None:
    template_ids = [
        "caregiver_participant_healthcare",
        "interview_psychology",
        "therapy_open_conversation",
    ]

    packs = [load_skill_pack(template_id) for template_id in template_ids]

    assert [pack.id for pack in packs] == template_ids
    assert all("concept_count_metrics" in [metric.id for metric in pack.metrics] for pack in packs)
    assert all(pack.concept_lexicons for pack in packs)
    assert all(pack.nonverbal_cues for pack in packs)


def test_load_skill_pack_by_file_path_preserves_output_schema(tmp_path: Path) -> None:
    pack_path = tmp_path / "custom_pack.json"
    pack_path.write_text(
        json.dumps(
            {
                "id": "custom_pack",
                "name": "Custom Pack",
                "version": "1.0.0",
                "metrics": [
                    {
                        "id": "base_metrics",
                        "output_schema": {
                            "type": "table",
                            "columns": ["speaker", "turns"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    pack = load_skill_pack(pack_path)

    assert pack.id == "custom_pack"
    assert pack.metrics[0].id == "base_metrics"
    assert pack.metrics[0].output_schema == {
        "type": "table",
        "columns": ["speaker", "turns"],
    }


def test_load_skill_pack_supports_dynamic_research_definitions(tmp_path: Path) -> None:
    pack_path = tmp_path / "dynamic_pack.json"
    pack_path.write_text(
        json.dumps(
            {
                "id": "dynamic_pack",
                "name": "Dynamic Pack",
                "version": "1.0.0",
                "metrics": ["base_metrics"],
                "speaker_roles": {
                    "caregiver": {
                        "label": "Care Partner",
                        "prefixes": ["CG", "Caregiver"],
                    },
                    "participant": {
                        "label": "Participant",
                        "prefixes": ["P", "Patient"],
                    },
                },
                "disfluency_tokens": ["um", "uh", "like"],
                "concept_lexicons": {
                    "pain": ["pain", "ache", "hurts"],
                    "medication": ["pill", "dose"],
                },
                "nonverbal_cues": {
                    "pause": ["pause", "long pause", "silence"],
                    "laughter": ["laughs", "laughing", "chuckles"],
                },
            }
        ),
        encoding="utf-8",
    )

    pack = load_skill_pack(pack_path)

    assert pack.speaker_roles == {
        "caregiver": "Care Partner",
        "participant": "Participant",
    }
    assert pack.speaker_prefixes == {
        "caregiver": ["CG", "Caregiver"],
        "participant": ["P", "Patient"],
    }
    assert pack.disfluency_tokens == ["um", "uh", "like"]
    assert pack.concept_lexicons == {
        "pain": ["pain", "ache", "hurts"],
        "medication": ["pill", "dose"],
    }
    assert pack.nonverbal_cues == {
        "pause": ["pause", "long pause", "silence"],
        "laughter": ["laughs", "laughing", "chuckles"],
    }


def test_load_skill_pack_rejects_invalid_dynamic_definitions(tmp_path: Path) -> None:
    pack_path = tmp_path / "bad_dynamic_pack.json"
    pack_path.write_text(
        json.dumps(
            {
                "id": "bad_dynamic_pack",
                "name": "Bad Dynamic Pack",
                "version": "1.0.0",
                "metrics": ["base_metrics"],
                "concept_lexicons": {
                    "pain": ["pain", 12],
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SkillPackValidationError, match="concept_lexicons"):
        load_skill_pack(pack_path)


def test_load_skill_pack_rejects_missing_required_fields(tmp_path: Path) -> None:
    pack_path = tmp_path / "missing_fields.json"
    pack_path.write_text(
        json.dumps(
            {
                "id": "missing_fields",
                "name": "Missing Fields",
                "metrics": ["base_metrics"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SkillPackValidationError, match="version"):
        load_skill_pack(pack_path)


def test_load_skill_pack_rejects_unknown_metric_ids(tmp_path: Path) -> None:
    pack_path = tmp_path / "unknown_metric.json"
    pack_path.write_text(
        json.dumps(
            {
                "id": "unknown_metric",
                "name": "Unknown Metric",
                "version": "1.0.0",
                "metrics": ["base_metrics", "not_registered"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SkillPackValidationError, match="not_registered"):
        load_skill_pack(pack_path)

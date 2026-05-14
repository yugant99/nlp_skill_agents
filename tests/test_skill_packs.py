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

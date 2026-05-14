import json
from pathlib import Path

from backend.storage.library_store import LibraryStore


def test_library_store_approves_skill_pack_and_metric_plugin(tmp_path: Path) -> None:
    store = LibraryStore(tmp_path)

    skill_pack_entry = store.approve_skill_pack(
        {
            "id": "care_pack",
            "name": "Care Pack",
            "version": "1.0.0",
            "metrics": ["base_metrics"],
        },
        reviewer="professor",
        notes="Demo ready.",
    )
    metric_entry = store.approve_metric_plugin(
        {
            "id": "question_type_metrics",
            "label": "Question Type Metrics",
            "version": "1.0.0",
        },
        reviewer="professor",
        notes="Validated on synthetic examples.",
    )

    assert skill_pack_entry.entry_type == "skill_pack"
    assert metric_entry.entry_type == "metric_plugin"
    assert (tmp_path / "library" / "skill_packs" / "care_pack-1_0_0.json").exists()
    assert (
        tmp_path
        / "library"
        / "metric_plugins"
        / "question_type_metrics-1_0_0.json"
    ).exists()

    entries = store.list_entries()
    assert [entry.entry_type for entry in entries] == ["metric_plugin", "skill_pack"]
    assert entries[0].approved_by == "professor"

    audit_events = store.audit_log.list_events()
    assert [event["event_type"] for event in audit_events] == [
        "library.skill_pack.approved",
        "library.metric_plugin.approved",
    ]
    assert audit_events[0]["metadata"]["notes"] == "Demo ready."

    manifest = json.loads(
        (tmp_path / "library" / "library_manifest.json").read_text(encoding="utf-8")
    )
    assert [entry["id"] for entry in manifest["entries"]] == [
        "question_type_metrics",
        "care_pack",
    ]

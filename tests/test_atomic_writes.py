import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.storage import atomic
from backend.storage.atomic import atomic_text_writer, atomic_write_text
from backend.storage.audit_log import AuditLogStore


def test_atomic_text_writer_keeps_old_file_until_commit(tmp_path: Path) -> None:
    destination = tmp_path / "artifact.json"
    destination.write_text("old", encoding="utf-8")

    with atomic_text_writer(destination) as output:
        output.write("new")
        assert destination.read_text(encoding="utf-8") == "old"

    assert destination.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []


def test_atomic_text_writer_preserves_old_file_when_generation_fails(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "artifact.json"
    destination.write_text("old", encoding="utf-8")

    with pytest.raises(RuntimeError, match="generation failed"):
        with atomic_text_writer(destination) as output:
            output.write("partial")
            raise RuntimeError("generation failed")

    assert destination.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []


def test_atomic_write_text_preserves_old_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "artifact.json"
    destination.write_text("old", encoding="utf-8")

    def fail_replace(source: Path | str, target: Path | str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(destination, "new")

    assert destination.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".artifact.json.*.tmp")) == []


def test_audit_log_preserves_complete_history_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AuditLogStore(tmp_path)
    first = store.record("study.created", "study", "study-one")

    def fail_replace(source: Path | str, target: Path | str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.record("study.updated", "study", "study-one")

    events = store.list_events()
    assert [event["id"] for event in events] == [first.id]
    assert list((tmp_path / "audit").glob(".events.jsonl.*.tmp")) == []


def test_audit_log_rejects_an_incomplete_existing_record(tmp_path: Path) -> None:
    store = AuditLogStore(tmp_path)
    store.audit_dir.mkdir(parents=True)
    store.events_path.write_text('{"partial":', encoding="utf-8")

    with pytest.raises(ValueError, match="incomplete record"):
        store.record("study.updated", "study", "study-one")

    assert store.events_path.read_text(encoding="utf-8") == '{"partial":'


def test_audit_log_serializes_writes_across_processes(tmp_path: Path) -> None:
    process_count = 8
    start_path = tmp_path / "start"
    worker = """
import sys
import time
from pathlib import Path
from backend.storage.audit_log import AuditLogStore

root = Path(sys.argv[1])
ready_path = Path(sys.argv[2])
start_path = Path(sys.argv[3])
worker_index = int(sys.argv[4])
ready_path.write_text("ready", encoding="utf-8")
while not start_path.exists():
    time.sleep(0.005)
AuditLogStore(root).record(
    "concurrent.event",
    "worker",
    str(worker_index),
    {"worker_index": worker_index},
)
"""
    processes = []
    ready_paths = []
    for index in range(process_count):
        ready_path = tmp_path / f"ready-{index}"
        ready_paths.append(ready_path)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    worker,
                    str(tmp_path),
                    str(ready_path),
                    str(start_path),
                    str(index),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    deadline = time.monotonic() + 10
    while not all(path.exists() for path in ready_paths):
        if time.monotonic() >= deadline:
            raise AssertionError("audit worker processes did not become ready")
        time.sleep(0.01)
    start_path.write_text("start", encoding="utf-8")
    for process in processes:
        _, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr

    events = AuditLogStore(tmp_path).list_events(limit=None)
    assert len(events) == process_count
    assert {event["metadata"]["worker_index"] for event in events} == set(
        range(process_count)
    )

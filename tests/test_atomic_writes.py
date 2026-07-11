from pathlib import Path

import pytest

from backend.storage import atomic
from backend.storage.atomic import atomic_text_writer, atomic_write_text


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

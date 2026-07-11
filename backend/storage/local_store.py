from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.analysis.pipeline import AnalysisRun
from backend.storage.atomic import atomic_text_writer, atomic_write_text


@dataclass(frozen=True)
class StoredRun:
    run_id: str
    run_dir: Path
    export_dir: Path
    results_json: Path


class LocalRunStore:
    def __init__(self, root: Path | str = "local_data") -> None:
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.exports_dir = self.root / "exports"
        self.db_path = self.root / "runs.sqlite3"

    def persist_run(self, run: AnalysisRun) -> StoredRun:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

        run_dir = self.runs_dir / run.run_id
        export_dir = self.exports_dir / run.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        export_dir.mkdir(parents=True, exist_ok=True)

        results_json = run_dir / "results.json"
        atomic_write_text(
            results_json,
            json.dumps(_run_to_payload(run), indent=2),
        )
        for result in run.results:
            _write_metric_csv(export_dir / f"{result.metric_id}.csv", result.rows)
        self._record_run(run)
        return StoredRun(
            run_id=run.run_id,
            run_dir=run_dir,
            export_dir=export_dir,
            results_json=results_json,
        )

    def export_path(self, run_id: str, filename: str) -> Path:
        return self.exports_dir / run_id / filename

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        self._ensure_schema()
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                select run_id, source_filename, created_at, metric_count
                from analysis_runs
                order by created_at desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "source_filename": row["source_filename"],
                "created_at": row["created_at"],
                "metric_count": row["metric_count"],
                "results_json": str(self.runs_dir / row["run_id"] / "results.json"),
                "export_dir": str(self.exports_dir / row["run_id"]),
            }
            for row in rows
        ]

    def _ensure_schema(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                create table if not exists analysis_runs (
                  run_id text primary key,
                  source_filename text not null,
                  created_at text not null,
                  metric_count integer not null
                )
                """
            )

    def _record_run(self, run: AnalysisRun) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                insert or replace into analysis_runs (
                  run_id,
                  source_filename,
                  created_at,
                  metric_count
                ) values (?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.source_filename,
                    run.created_at,
                    len(run.results),
                ),
            )


def _run_to_payload(run: AnalysisRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "source_filename": run.source_filename,
        "created_at": run.created_at,
        "participant_id": run.transcript.config.participant_id,
        "skill_pack": _skill_pack_payload(run),
        "turn_count": len(run.transcript.turns),
        "results": [asdict(result) for result in run.results],
    }


def _skill_pack_payload(run: AnalysisRun) -> dict[str, str] | None:
    config = run.transcript.config
    if not config.skill_pack_id:
        return None
    return {
        "id": config.skill_pack_id,
        "name": config.skill_pack_name,
        "version": config.skill_pack_version,
    }


def _write_metric_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = _ordered_fieldnames(rows)
    with atomic_text_writer(path, newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _ordered_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames

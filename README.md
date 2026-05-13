# NLP Skill Agents

Local-first transcript analysis workbench for research studies.

The v1 system supports one transcript per run, guided study configuration, and three deterministic analysis skills:

- base transcript metrics
- lexical metrics
- disfluency metrics

All uploaded transcripts and generated outputs stay in `local_data/`, which is intentionally ignored by git.

## Project Layout

```text
backend/               FastAPI app and deterministic analysis engine
frontend/              React + Tailwind research workbench
study_skill_packs/     Product-facing study and metric skill definitions
codex_internal_skills/ Internal development skills for Codex-driven work
tests/                 Backend unit and integration tests
local_data/            Local uploads, runs, SQLite DB, and exports
```

## Development

Backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn backend.app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```


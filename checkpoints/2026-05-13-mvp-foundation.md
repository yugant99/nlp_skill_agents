# 2026-05-13 MVP Foundation

## Goal

Establish the local-first NLP Skill Agents MVP: transcript upload/paste, deterministic transcript metrics, local storage/export, skill-pack metadata, and a compact React workbench.

## Completed

- FastAPI backend scaffold.
- TXT/DOCX transcript extraction.
- Speaker-turn parsing.
- Base metrics.
- Lexical metrics.
- Disfluency metrics.
- Transcript diagnostics.
- Local SQLite run metadata.
- Local JSON/CSV outputs under ignored storage.
- Default study skill pack.
- Upload workflow.
- Paste-transcript workflow.
- Configurable speaker prefixes.
- Metric selection.
- CSV export links.
- Recent local runs panel.
- Frontend workbench with summary cards and result tables.

## Verification

Last known full verification before this checkpoint:

- Backend: `pytest`
- Frontend: `npm run lint && npm run build`
- UI: local workbench walkthrough at `http://127.0.0.1:5173/`

## Git

Latest known stable commit when this checkpoint was created:

- `11e9db9 fix: return bad request for unknown metrics`

## Follow-Up Risks

- Current metric definitions are mostly hardcoded.
- Dynamic researcher-defined skill packs are the next architecture priority.
- Multi-file batch support is not implemented yet.
- UI verification should be repeated after every new user-facing feature.

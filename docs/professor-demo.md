# Four-Luna Professor Demo

This is a deliberately bounded classroom proof, not a claim that the qualitative
research roadmap or Phase 4 is complete.

One browser action:

1. accepts a short transcript explicitly declared as synthetic;
2. performs exactly four one-shot OpenRouter calls to `openai/gpt-5.6-luna`;
3. pins every call to `azure/eu` with ZDR required and fallbacks disabled;
4. validates four role-specific strict JSON Schema results;
5. combines the validated fields deterministically in the local backend;
6. writes a local run snapshot with native token and cost accounting; and
7. leaves the result as a candidate until a separate human Accept action.

The four specialists are fixed: speaker turns, timing and pauses, speech repairs
and overlap, and redaction plus nonverbal cues. There is no retry, fallback,
response healing, judge, or fifth model call. A failed strict result remains a
visible failed run and cannot be accepted.

## Data boundary

The UI, validation, merge, receipt, and revision store are local. The four Luna
inference calls are remote through OpenRouter, so only the bundled synthetic
sample should be used. Do not paste participant or research data into this demo.

Run completion does not change accepted state. Accept activates local revision 1
while preserving immutable revision 0 and both SHA-256 digests. Restore switches
the active pointer back to revision 0 without deleting the generated revision.
No study record or qualitative research database is changed.

## Run locally

From the repository root, configure the backend-only key in ignored `.env`:

```text
OPENROUTER_API_KEY=replace-with-a-low-limit-demo-key
```

Start the backend and keep demo artifacts in an isolated ignored directory:

```bash
export NLP_SKILL_AGENTS_DATA_DIR="$PWD/local_data/professor_demo_live"
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173/professor-demo` and use the bundled sample. Before the
four paid calls, the backend validates the key, the current endpoint contract,
ZDR status, required structured-output parameters, bounded request size, and a
worst-case cost below `$0.25`.

The completed screen must show four distinct strict results, four attempted and
completed calls, a native usage/cost receipt, the original and local candidate,
and the still-inactive human gate. Only then should Accept become available.

## Focused verification

```bash
.venv/bin/python -m pytest -q \
  tests/test_professor_demo.py \
  tests/test_professor_demo_api.py \
  tests/test_openrouter_client.py

cd frontend
npm run test:professor-demo
npm run build
```

After a live classroom run, rotate the demo key. A key pasted into chat or logs
must be treated as exposed even when it never enters a tracked file.


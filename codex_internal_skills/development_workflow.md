# Development Workflow Skill

When building this repo:

1. Read the relevant code and internal skill docs before editing. Follow existing repo patterns.
2. Keep changes modular and limited to the requested ownership boundary.
3. Write tests before deterministic analysis behavior, especially metric logic and normalization rules.
4. Prefer declarative study skills over arbitrary runtime code.
5. Preserve local-only outputs: keep transcripts, generated reports, screenshots, scratch evaluations, and cached runs in `local_data/` or another ignored local path.
6. Do not commit product runtime changes when the task is only to update internal Codex guidance.
7. Before commit or push, run the verification that matches the change:
   - Backend behavior: focused tests, then the broader backend suite when shared behavior is touched.
   - Frontend behavior: lint/build, then local browser inspection for layout and console errors.
   - Documentation-only changes: inspect rendered Markdown where useful, run lightweight checks, and verify diff scope.
8. Check `git status --short` and review the diff before claiming completion. Confirm no local-only outputs, generated artifacts, or unrelated edits are included.

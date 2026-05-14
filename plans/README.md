# Plans

This folder tracks implementation plans beyond the high-level roadmap. Each plan is written so autonomous agents can work in small verified slices while keeping `master` demoable.

## Current Plan Stack

- [V3 Agentic Extension System](v3-agentic-extension-system.md)
- [V4 Study Workspace and Dashboard Composer](v4-study-workspace-dashboard-composer.md)
- [V5 Local Orchestration and Review Gates](v5-local-orchestration-review-gates.md)
- [V6 Institutional Research Platform](v6-institutional-research-platform.md)

## Working Rules

- Build the same flow deeper at each version: study setup -> skill pack -> metric/plugin request -> verified local run -> dashboard-ready outputs.
- Keep transcript content local unless a future workflow explicitly asks for external processing and records consent.
- Commit and push every stable feature slice after tests and UI verification.
- Prefer config skills before code plugins. Use code plugins only when the requested analysis cannot be expressed through skill-pack fields.

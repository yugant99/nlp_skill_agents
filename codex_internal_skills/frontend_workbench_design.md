# Frontend Workbench Design Skill

Use this when building or revising frontend workbench screens for this repo.

## Goal

Workbench UI should feel purpose-built for analysis and iteration, not like a generic admin dashboard or template landing page.

## Design Principles

1. Start from the user's active job: reviewing analysis, comparing metrics, debugging outputs, or authoring skills. Make that job visible in the first viewport.
2. Use dense, scannable layouts with clear hierarchy. Avoid marketing hero sections, decorative cards, and filler panels.
3. Give each screen one memorable organizing idea: a timeline, matrix, split review surface, trace view, rubric editor, or comparison bench.
4. Use controls that match the action: tabs for views, segmented controls for modes, sliders or numeric inputs for thresholds, toggles for binary settings, and icon buttons for common tools.
5. Preserve visual variety through layout, typography scale, spacing, and meaningful status color. Do not let the page collapse into a one-note palette.

## Non-Boilerplate Checklist

Before calling a workbench screen complete, verify:

- The screen would still be recognizable if the product name were removed.
- The primary workflow is usable without reading explanatory feature text.
- Repeated cards or tables expose real distinctions between items, not identical placeholder structure.
- Empty, loading, error, and dense-data states are designed.
- Text fits at mobile and desktop widths without overlap.

## Local Outputs

Screenshots, visual experiments, generated assets, and throwaway browser artifacts belong in local-only ignored paths such as `local_data/`. Do not commit exploratory renders unless the repo already has an intentional fixture or asset path for them.

## Verification Before Commit Or Push

1. Run the frontend lint/build command used by the repo.
2. Open the workbench locally and inspect desktop and mobile widths.
3. Check for console errors, blank states, broken assets, and overlapping text.
4. Run `git status --short` and confirm only intended source or documentation files changed.

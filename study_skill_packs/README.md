# Study Skill Packs

Product-facing skill definitions live here. These files describe what a study can run: metric IDs, default cleaning rules, output schemas, and export behavior.

V1 keeps these skills declarative. Custom Python plugins are intentionally out of scope until the local workflow is stable and auditable.

Dynamic V1 skill packs can define:

- speaker roles and prefixes
- disfluency inventories
- concept lexicons
- nonverbal cue categories
- metric IDs to run

Built-in templates:

- `default_transcript_metrics.json`
- `caregiver_participant_healthcare.json`
- `interview_psychology.json`
- `therapy_open_conversation.json`

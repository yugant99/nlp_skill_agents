# Healthcare Demo Assets

Synthetic, non-identifiable assets for the professor demo.

## Demo Story

1. Load `skill_pack.json` in the Skill Pack Studio.
2. Run `transcripts/participant_001.txt` as a single-transcript analysis.
3. Use the Study Workspace panel with all three transcript files to run a batch.
4. Point out the casebook metadata columns: participant, condition, and week.
5. Show aggregate JSON/CSV outputs, then export a reproducibility bundle.
6. Queue a metric plugin job and record evidence to show the agentic extension workflow.

## Casebook Setup

The Study Workspace batch paste format now supports deterministic file assignment:

```text
filename.txt | participant_id=P1 | condition=home | week=week_1
```

These metadata fields are copied into every aggregate metric row. This is the NVivo-like casebook layer: uploaded files become comparable cases across participants, conditions, weeks, and optional custom fields.

## Transcript Blocks For Batch Paste

```text
participant_001.txt | participant_id=P1 | condition=home | week=week_1
CG: How did walking feel after lunch?
P: Um, my knee hurt when I stood up.
CG: I will call the clinic tomorrow and ask about the medication review.
P: That would help. [pause]
---
participant_002.txt | participant_id=P2 | condition=lab | week=week_1
CG: Did the new pill help you sleep?
P: Yes, I slept longer but felt dizzy.
CG: We can schedule a balance check next week.
P: I am worried about falling again.
---
participant_003.txt | participant_id=P3 | condition=home | week=week_2
CG: What makes the pain worse in the morning?
P: Walking to the bathroom makes it sore.
CG: Let's arrange transportation for the appointment.
P: Thank you, I forgot the appointment time.
```

import type { BatchTranscript } from "./types";

export type CasebookOptions = {
  participants: string[];
  conditions: string[];
  weeks: string[];
};

export function buildCasebookOptions(
  participantCount: number,
  conditionText: string,
  weekCount: number
): CasebookOptions {
  const boundedParticipants = Math.min(Math.max(Math.trunc(participantCount) || 1, 1), 4);
  const boundedWeeks = Math.max(Math.trunc(weekCount) || 1, 1);
  return {
    participants: Array.from({ length: boundedParticipants }, (_, index) => `P${index + 1}`),
    conditions: normalizeConditionList(conditionText),
    weeks: Array.from({ length: boundedWeeks }, (_, index) => `week_${index + 1}`)
  };
}

export function normalizeConditionList(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(",")
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean)
    )
  );
}

export function validateBatchAssignments(
  transcripts: BatchTranscript[],
  options: CasebookOptions
): string[] {
  const warnings: string[] = [];
  for (const transcript of transcripts) {
    const metadata = transcript.metadata ?? {};
    if (
      metadata.participant_id &&
      !options.participants.includes(metadata.participant_id)
    ) {
      warnings.push(
        `${transcript.source_filename} uses participant ${metadata.participant_id} outside ${rangeLabel(options.participants)}.`
      );
    }
    if (metadata.condition && !options.conditions.includes(metadata.condition)) {
      warnings.push(
        `${transcript.source_filename} uses condition ${metadata.condition} outside ${listLabel(options.conditions)}.`
      );
    }
    if (metadata.week && !options.weeks.includes(metadata.week)) {
      warnings.push(
        `${transcript.source_filename} uses week ${metadata.week} outside ${rangeLabel(options.weeks)}.`
      );
    }
  }
  return warnings;
}

function rangeLabel(values: string[]): string {
  if (!values.length) {
    return "the configured range";
  }
  if (values.length === 1) {
    return values[0];
  }
  return `${values[0]}-${values[values.length - 1]}`;
}

function listLabel(values: string[]): string {
  return values.length ? values.join(", ") : "the configured conditions";
}

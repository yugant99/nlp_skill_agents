import type { BatchTranscript } from "./types";
import type { StudySchema } from "./types";

export type CasebookOptions = {
  participants: string[];
  conditions: string[];
  weeks: string[];
};

export type CasebookTemplate = {
  id: string;
  label: string;
  participantCount: number;
  conditions: string;
  weekCount: number;
  customFields: string[];
};

export type CasebookControls = {
  participantCount: number;
  conditions: string;
  weekCount: number;
  customFields: string;
};

export type StudySchemaRequestPayload = {
  participant_count: number;
  conditions: string[];
  week_count: number;
  custom_fields: string[];
};

export const CASEBOOK_TEMPLATES: Record<string, CasebookTemplate> = {
  caregiver_mobility: {
    id: "caregiver_mobility",
    label: "Caregiver mobility",
    participantCount: 4,
    conditions: "home, lab, clinic",
    weekCount: 4,
    customFields: ["site", "study_arm"]
  },
  interview: {
    id: "interview",
    label: "Interview study",
    participantCount: 4,
    conditions: "baseline, followup",
    weekCount: 2,
    customFields: ["interviewer", "site"]
  },
  therapy_session: {
    id: "therapy_session",
    label: "Therapy session",
    participantCount: 4,
    conditions: "individual, group, telehealth",
    weekCount: 8,
    customFields: ["clinician", "session_type"]
  }
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

export function casebookRequestFromControls(
  controls: CasebookControls
): StudySchemaRequestPayload {
  return {
    participant_count: Math.min(Math.max(Math.trunc(controls.participantCount) || 1, 1), 4),
    conditions: normalizeConditionList(controls.conditions),
    week_count: Math.max(Math.trunc(controls.weekCount) || 1, 1),
    custom_fields: normalizeConditionList(controls.customFields)
  };
}

export function schemaControlsFromStudySchema(schema: StudySchema): CasebookControls {
  return {
    participantCount: schema.participant_count,
    conditions: schema.conditions.join(", "),
    weekCount: schema.week_count,
    customFields: schema.custom_fields.join(", ")
  };
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

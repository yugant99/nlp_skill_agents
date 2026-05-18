import type { BatchTranscript } from "./types";

const METADATA_ORDER = ["participant_id", "condition", "week"];

export function parseBatchTranscriptText(value: string): BatchTranscript[] {
  const transcripts = value
    .split(/\n---+\n/g)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block, index) => {
      const [headerLine, ...contentLines] = block.split("\n");
      const [filenamePart, ...metadataParts] = headerLine
        .split("|")
        .map((part) => part.trim());
      const metadata = parseHeaderMetadata(metadataParts);
      return {
        source_filename: filenamePart || `transcript_${index + 1}.txt`,
        content: contentLines.join("\n").trim(),
        ...(Object.keys(metadata).length ? { metadata } : {})
      };
    })
    .filter((item) => item.content);
  if (!transcripts.length) {
    throw new Error("Add at least one transcript block with a filename and content.");
  }
  return transcripts;
}

export function updateBatchTranscriptMetadata(
  transcripts: BatchTranscript[],
  index: number,
  key: string,
  value: string
): BatchTranscript[] {
  return transcripts.map((transcript, currentIndex) => {
    if (currentIndex !== index) {
      return transcript;
    }
    const metadata = { ...(transcript.metadata ?? {}) };
    const normalizedValue = value.trim();
    if (normalizedValue) {
      metadata[key] = normalizedValue;
    } else {
      delete metadata[key];
    }
    return {
      ...transcript,
      ...(Object.keys(metadata).length ? { metadata } : { metadata: undefined })
    };
  });
}

export function serializeBatchTranscriptText(transcripts: BatchTranscript[]): string {
  return transcripts
    .map((transcript) => {
      const metadata = orderedMetadata(transcript.metadata ?? {});
      const headerParts = [
        transcript.source_filename,
        ...Object.entries(metadata).map(([key, value]) => `${key}=${value}`)
      ];
      return `${headerParts.join(" | ")}\n${transcript.content}`.trim();
    })
    .join("\n---\n");
}

function parseHeaderMetadata(parts: string[]): Record<string, string> {
  return Object.fromEntries(
    parts
      .map((part) => {
        const [key, ...valueParts] = part.split("=");
        return [key?.trim() ?? "", valueParts.join("=").trim()];
      })
      .filter(([key, value]) => key && value)
  );
}

function orderedMetadata(metadata: Record<string, string>): Record<string, string> {
  const ordered: Record<string, string> = {};
  for (const key of METADATA_ORDER) {
    if (metadata[key]) {
      ordered[key] = metadata[key];
    }
  }
  for (const key of Object.keys(metadata).sort()) {
    if (!ordered[key]) {
      ordered[key] = metadata[key];
    }
  }
  return ordered;
}

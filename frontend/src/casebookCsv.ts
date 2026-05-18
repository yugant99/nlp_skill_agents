import type { BatchTranscript } from "./types";

const BASE_COLUMNS = ["source_filename", "participant_id", "condition", "week"];

export function exportCasebookCsv(transcripts: BatchTranscript[]): string {
  const customColumns = Array.from(
    new Set(
      transcripts.flatMap((transcript) =>
        Object.keys(transcript.metadata ?? {}).filter((key) => !BASE_COLUMNS.includes(key))
      )
    )
  ).sort();
  const columns = [...BASE_COLUMNS, ...customColumns];
  const rows = transcripts.map((transcript) =>
    columns.map((column) => {
      if (column === "source_filename") {
        return transcript.source_filename;
      }
      return normalizedMetadataValue(transcript.metadata?.[column]);
    })
  );
  return [columns, ...rows].map((row) => row.map(escapeCsvCell).join(",")).join("\n");
}

export function parseCasebookCsv(value: string): Record<string, Record<string, string>> {
  const rows = parseCsvRows(value);
  if (!rows.length) {
    return {};
  }
  const [rawHeaders, ...dataRows] = rows;
  const headers = rawHeaders.map((header) => header.trim());
  const sourceFilenameIndex = headers.indexOf("source_filename");
  if (sourceFilenameIndex === -1) {
    throw new Error("Casebook CSV must include a source_filename column.");
  }

  const metadataByFilename: Record<string, Record<string, string>> = {};
  for (const row of dataRows) {
    const sourceFilename = (row[sourceFilenameIndex] ?? "").trim();
    if (!sourceFilename) {
      continue;
    }
    const metadata: Record<string, string> = {};
    headers.forEach((header, index) => {
      if (!header || header === "source_filename") {
        return;
      }
      const normalizedValue = normalizedMetadataValue(row[index]);
      if (normalizedValue) {
        metadata[header] = normalizedValue;
      }
    });
    metadataByFilename[sourceFilename] = metadata;
  }
  return metadataByFilename;
}

function normalizedMetadataValue(value: string | undefined): string {
  return (value ?? "").trim();
}

function escapeCsvCell(value: string): string {
  if (!/[",\n\r]/.test(value)) {
    return value;
  }
  return `"${value.replace(/"/g, '""')}"`;
}

function parseCsvRows(value: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;

  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    const nextChar = value[index + 1];

    if (inQuotes) {
      if (char === '"' && nextChar === '"') {
        cell += '"';
        index += 1;
      } else if (char === '"') {
        inQuotes = false;
      } else {
        cell += char;
      }
      continue;
    }

    if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      row.push(cell);
      cell = "";
    } else if (char === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else if (char === "\r") {
      if (nextChar === "\n") {
        index += 1;
      }
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }

  if (inQuotes) {
    throw new Error("Casebook CSV has an unterminated quoted cell.");
  }
  if (cell || row.length || value.endsWith(",")) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((currentRow) => currentRow.some((currentCell) => currentCell.trim()));
}

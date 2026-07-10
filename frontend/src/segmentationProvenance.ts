import type { SegmentationSource } from "./types";

export function segmentationSourceLabel(source: SegmentationSource): string {
  return source === "synthetic"
    ? "Synthetic source"
    : "Researcher-provided source";
}

import type { SegmentationValidationProfile } from "./types";

type RuleCheckCounts = {
  configured_rule_count: number;
  passed_rule_count: number;
};

export const RULE_CHECK_LIMIT_TEXT =
  "Deterministic rule checks only; not an accuracy, agreement, or calibrated-confidence estimate.";

export function ruleCheckSummary(counts: RuleCheckCounts): string {
  if (counts.configured_rule_count === 0) {
    return "No rule checks configured";
  }
  return `${counts.passed_rule_count}/${counts.configured_rule_count} rule checks`;
}

export function validationStatusLabel(
  profile: Pick<SegmentationValidationProfile, "status">
): string {
  return profile.status === "not_domain_validated"
    ? "Not domain validated"
    : "Validation status unavailable";
}

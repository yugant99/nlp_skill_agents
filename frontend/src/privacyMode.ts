import type { DeploymentProfile } from "./types";

export function privacyModeLabel(profile: DeploymentProfile): string {
  const networkCheck = profile.checks.find(
    (check) => check.id === "network_llm_disabled"
  );
  if (!networkCheck) {
    return "Status unavailable";
  }
  return networkCheck.status === "passed"
    ? "Offline ready"
    : "External authoring configured";
}

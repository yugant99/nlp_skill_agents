import type { AgentJob, AgentJobTransition } from "./types";

const transitionLabels: Record<AgentJobTransition, string> = {
  in_progress: "Start",
  blocked: "Block",
  verified: "Verify",
  merged: "Merge"
};

export function agentJobActions(
  job: Pick<AgentJob, "available_transitions">
): { status: AgentJobTransition; label: string }[] {
  return job.available_transitions.map((status) => ({
    status,
    label: transitionLabels[status]
  }));
}

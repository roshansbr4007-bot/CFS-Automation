import type { Task, TaskPriority, TaskStatus, VerificationStatus } from "../../api/types";

/** Display labels only. Blocked is shown as "On hold"; the stored status stays BLOCKED. */
export const STATUS_LABEL: Record<TaskStatus, string> = {
  PENDING: "Pending",
  IN_PROGRESS: "In progress",
  BLOCKED: "On hold",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
};

export const PRIORITY_LABEL: Record<TaskPriority, string> = {
  LOW: "Low",
  MEDIUM: "Medium",
  HIGH: "High",
  URGENT: "Urgent",
};

/** Change Set 1 (D1): task screens show URGENT as "Critical". The stored value stays URGENT and
 * PRIORITY_LABEL above is unchanged (other screens, including frozen Phase 9 pages, use it). */
export const TASK_PRIORITY_LABEL: Record<TaskPriority, string> = { ...PRIORITY_LABEL, URGENT: "Critical" };

export const VERIFICATION_LABEL: Record<VerificationStatus, string> = {
  NOT_REQUIRED: "Not required",
  PENDING: "Awaiting verification",
  VERIFIED: "Verified",
  REJECTED: "Rejected — rework",
};

export function acknowledgmentLabel(task: Pick<Task, "acknowledgment_required" | "acknowledged_at">): string {
  if (!task.acknowledgment_required) return "Not required";
  return task.acknowledged_at ? "Acknowledged" : "Waiting";
}

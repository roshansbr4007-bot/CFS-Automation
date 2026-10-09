import type { OverdueCause, OverdueOpenedVia, OverdueStatus } from "../../api/types";

/** The backend's own choice labels (apps/overdue/models.py). */
export const OVERDUE_STATUS_LABEL: Record<OverdueStatus, string> = {
  OPEN: "Reason needed",
  REASON_SUBMITTED: "Reason submitted",
  REVIEWED: "Reviewed",
};

export const OVERDUE_CAUSE_LABEL: Record<OverdueCause, string> = {
  DEPENDENCY: "Dependency",
  EMPLOYEE: "Employee",
  SYSTEM: "System",
  CLIENT: "Client",
  OTHER: "Other",
};

export const OPENED_VIA_LABEL: Record<OverdueOpenedVia, string> = {
  TICK: "SLA checker",
  COMPLETION: "Completed after the deadline",
  REPAIR: "Repair command",
};

/** 0 -> "0 min", 75 -> "1 h 15 min", 1500 -> "1 d 1 h". */
export function formatOverdueMinutes(minutes: number): string {
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return hours > 0 ? `${days} d ${hours} h` : `${days} d`;
  if (hours > 0) return mins > 0 ? `${hours} h ${mins} min` : `${hours} h`;
  return `${mins} min`;
}

/** Phase 7.5A: display labels for the configuration choice values. The VALUES mirror the backend
 * TextChoices in apps/performance/models.py (the backend validates every value it receives; a
 * value missing here is shown as-is, never rejected by the screen). */
import { ROLE_NAMES } from "../../api/types";
import { formatBusinessDate } from "../../components/DateTimeText";

type Options = readonly (readonly [string, string])[];

export const STATUS_LABEL: Record<string, string> = { DRAFT: "Draft", ACTIVE: "Active", RETIRED: "Retired" };
export const STATUS_COLOR: Record<string, "default" | "success" | "warning"> = {
  DRAFT: "warning", ACTIVE: "success", RETIRED: "default",
};
export const MODEL_LABEL: Record<string, string> = {
  LEGACY_WEIGHTED: "Legacy (0–100), read-only",
  KRA_POINTS: "KRA points (0–10)",
};

export const SOURCE_TYPES: Options = [
  ["RESPONSIBILITY_TASKS", "Tasks of a responsibility"],
  ["MANUAL_ENTRY", "Manual entry (scored by HR)"],
];
export const TASK_SCOPES: Options = [
  ["SCHEDULED", "Scheduled tasks"],
  ["MANUAL", "Manual tasks"],
  ["BOTH", "Scheduled and manual tasks"],
];
export const MANUAL_MATCHES: Options = [
  ["NONE", "No automatic rule"],
  ["TASK_TYPE", "Same task type as the responsibility"],
  ["CATEGORY", "Same category and department as the responsibility"],
];
export const VERIFICATION_POLICIES: Options = [
  ["NOT_REQUIRED", "Completion counts"],
  ["REQUIRED", "Counts only once verified"],
];
export const DEDUCTION_KINDS: Options = [
  ["PERCENT_RANGE", "Percentage reduction within a range"],
  ["BAND_CEILING", "Band ceiling (special penalty)"],
];
export const DEDUCTION_SCOPES: Options = [
  ["COMPONENT", "Component"],
  ["KPI", "KPI"],
  ["OVERALL", "Overall"],
];
export const DEDUCTION_STACKING: Options = [
  ["STACK", "Stacks with other deductions"],
  ["NON_STACKING", "Does not stack"],
];
export const STACKING_METHODS: Options = [
  ["ADDITIVE", "Additive"],
  ["SEQUENTIAL", "Sequential (each applies to the remainder)"],
];
export const SYSTEM_ROLES = ROLE_NAMES;

export const RESOLUTION_STATE_LABEL: Record<string, string> = {
  RESOLVED: "A plan applies",
  NO_PLAN: "No plan applies",
  AMBIGUOUS_ROLE: "Ambiguous: more than one default matches (HR must set an override)",
};
export const RESOLUTION_SOURCE_LABEL: Record<string, string> = {
  OVERRIDE: "HR override",
  DEFAULT: "Department + system role default",
};

export function labelOf(options: Options, value: string | null | undefined): string {
  if (!value) return "—";
  return options.find(([key]) => key === value)?.[1] ?? value;
}

/** "3.000000" -> "3", "0.250000" -> "0.25": display only (the stored value is never changed). */
export function plain(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
}

export function planTitle(plan: { configuration: string; version: number; name: string }): string {
  return `${plan.name} (${plan.configuration} v${plan.version})`;
}

export function period(from: string, to: string | null): string {
  return to ? `${formatBusinessDate(from)} to ${formatBusinessDate(to)}` : `${formatBusinessDate(from)} onwards`;
}

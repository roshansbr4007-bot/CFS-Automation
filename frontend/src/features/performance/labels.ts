import type { KraAnnualExclusion, KraEmployeeState } from "../../api/types";

export const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"] as const;
export const monthName = (month: number) => MONTH_NAMES[month - 1] ?? String(month);
export const monthShort = (month: number) => monthName(month).slice(0, 3);

export const STATE_LABEL: Record<KraEmployeeState, string> = {
  PROVISIONAL: "Provisional",
  PENDING_REVIEW: "Under review – score pending",
  FINALIZED: "Finalized",
};

export const EXCLUSION_LABEL: Record<KraAnnualExclusion, string> = {
  NO_RECORD: "No result",
  NOT_FINALIZED: "Not finalized yet",
  LEGACY_SCALE: "Earlier 0–100 scale",
  NOTHING_APPLICABLE: "Nothing applicable",
};

export const STATUS_LABEL: Record<string, string> = {
  DRAFT: "Draft",
  CALCULATED: "Calculated",
  UNDER_REVIEW: "Under review",
  FINALIZED: "Finalized",
};

/** Stored values keep 6 decimals; reports and screens show 2 (section 13). */
export function points(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "—";
}

/** The KRA scale is fixed at 10 points (section 13). */
export const KRA_SCALE = 10;

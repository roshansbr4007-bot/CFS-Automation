import { Chip } from "@mui/material";

import type { SlaClock, SlaState } from "../../api/types";

export const SLA_LABEL: Record<SlaState, string> = {
  NOT_STARTED: "Not started",
  ON_TRACK: "On track",
  WARNING: "Warning",
  CRITICAL: "Critical",
  OVERDUE: "Overdue",
};

const COLOR: Record<SlaState, "default" | "success" | "warning" | "error"> = {
  NOT_STARTED: "default",
  ON_TRACK: "success",
  WARNING: "warning",
  CRITICAL: "error",
  OVERDUE: "error",
};

/** Just the backend's SLA state (Phase 5.1 daily activities / monitoring). */
export function SlaStateChip({ state }: { state: SlaState | null }) {
  if (!state) return <Chip size="small" variant="outlined" label="No SLA" />;
  return <Chip size="small" color={COLOR[state]} variant={state === "OVERDUE" ? "filled" : "outlined"} label={SLA_LABEL[state]} />;
}

/** Shows the backend's SLA state. It never decides the state itself. */
export function SlaBadge({ clock, note }: { clock: SlaClock | null; note?: string | null }) {
  if (!clock) return <Chip size="small" variant="outlined" label={note ? "No SLA" : "—"} title={note ?? undefined} />;
  if (clock.outcome) {
    return <Chip size="small" color={clock.outcome === "MET" ? "success" : "error"} label={clock.outcome === "MET" ? "SLA met" : "SLA missed"} />;
  }
  return (
    <Chip
      size="small"
      color={COLOR[clock.state]}
      variant={clock.state === "OVERDUE" ? "filled" : "outlined"}
      label={SLA_LABEL[clock.state]}
    />
  );
}

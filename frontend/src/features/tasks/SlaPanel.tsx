import { Box, LinearProgress, Stack, Typography } from "@mui/material";

import type { SlaClock, TaskSla } from "../../api/types";
import { DateTimeText } from "../../components/DateTimeText";
import { SlaBadge } from "./SlaBadge";

function duration(minutes: number | null): string {
  if (minutes === null) return "—";
  if (minutes % 60 === 0) return `${minutes / 60} hour${minutes === 60 ? "" : "s"}`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

const MARKS = [
  { pct: 50, label: "Warning" },
  { pct: 75, label: "Critical" },
  { pct: 100, label: "Overdue" },
];

/** Progress against the backend-calculated deadline. Values come only from the API. */
export function SlaProgress({ clock }: { clock: SlaClock }) {
  const pct = clock.elapsed_pct ?? 0;
  const shown = Math.min(pct, 100);
  const color = clock.state === "ON_TRACK" || clock.state === "NOT_STARTED" ? "success" : clock.state === "WARNING" ? "warning" : "error";
  return (
    <Box aria-label={`${pct}% of SLA used`}>
      <Box sx={{ position: "relative", mt: 1 }}>
        <LinearProgress variant="determinate" value={shown} color={color} sx={{ height: 10, borderRadius: 5 }} />
        {MARKS.map((m) => (
          <Box key={m.pct} sx={{ position: "absolute", top: -3, left: `calc(${m.pct}% - 1px)`, width: 2, height: 16, bgcolor: "text.secondary" }} />
        ))}
      </Box>
      <Box sx={{ position: "relative", height: 18, mt: 0.5 }}>
        {MARKS.map((m) => (
          <Typography key={m.pct} variant="caption" color="text.secondary"
            sx={{ position: "absolute", left: `${m.pct}%`, transform: m.pct === 100 ? "translateX(-100%)" : "translateX(-50%)" }}>
            {m.pct}% {m.label}
          </Typography>
        ))}
      </Box>
    </Box>
  );
}

function ClockBlock({ title, clock }: { title: string; clock: SlaClock }) {
  return (
    <Stack spacing={0.75}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Typography variant="subtitle2">{title}</Typography>
        <SlaBadge clock={clock} />
      </Stack>
      <Typography variant="body2" color="text.secondary">{clock.rule_name} · {duration(clock.duration_minutes)}</Typography>
      {clock.waiting_for ? (
        <Typography variant="body2">{clock.waiting_for}</Typography>
      ) : (
        <>
          <Typography variant="body2">SLA start: <DateTimeText value={clock.start_at} /></Typography>
          <Typography variant="body2">Due: <strong><DateTimeText value={clock.due_at} /></strong></Typography>
          {clock.elapsed_pct !== null && (
            <Typography variant="body2">Elapsed: {clock.elapsed_pct}%{clock.stopped_at ? " (stopped)" : ""}</Typography>
          )}
          {clock.start_at && clock.elapsed_pct !== null && <SlaProgress clock={clock} />}
        </>
      )}
    </Stack>
  );
}

export function SlaPanel({ sla }: { sla: TaskSla }) {
  return (
    <Stack spacing={2}>
      {sla.resolution ? (
        <ClockBlock title="Resolution SLA" clock={sla.resolution} />
      ) : (
        <Typography variant="body2" color="text.secondary">{sla.resolution_note ?? "No SLA configured"}</Typography>
      )}
      {sla.acknowledgment && <ClockBlock title="Acknowledgement SLA" clock={sla.acknowledgment} />}
    </Stack>
  );
}

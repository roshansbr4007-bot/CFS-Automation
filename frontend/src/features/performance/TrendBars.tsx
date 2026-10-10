import { Box, Paper, Stack, Typography } from "@mui/material";

import type { KraMyMonthRow } from "../../api/types";
import { KRA_SCALE, monthName, monthShort, points } from "./labels";

const CHART_HEIGHT = 140;

function describe(month: number, row: KraMyMonthRow | undefined): string {
  if (!row) return `${monthName(month)}: no result`;
  if (row.state === "PENDING_REVIEW") return `${monthName(month)}: under review, score pending`;
  const kind = row.state === "PROVISIONAL" ? "provisional" : "finalized";
  return `${monthName(month)}: ${points(row.final_total)} of ${KRA_SCALE}, ${kind}`;
}

/** Monthly trend of one calendar year on the fixed 10-point scale, drawn with MUI boxes (no
 * chart library): finalized months solid, the provisional month outlined, months under review
 * labelled "Pending", months without a result left empty (never drawn as 0). */
export function TrendBars({ year, months }: { year: number; months: KraMyMonthRow[] }) {
  const byMonth = new Map(months.filter((m) => m.year === year).map((m) => [m.month, m]));
  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="h6" component="h3" sx={{ mb: 1 }}>Monthly trend {year}</Typography>
      <Stack direction="row" spacing={1} role="list" aria-label={`Monthly trend ${year}`}
        sx={{ alignItems: "flex-end", overflowX: "auto" }}>
        {Array.from({ length: 12 }, (_, index) => index + 1).map((month) => {
          const row = byMonth.get(month);
          const shown = row && row.state !== "PENDING_REVIEW" && row.final_total !== null;
          const value = shown ? Math.max(0, Math.min(KRA_SCALE, Number(row.final_total))) : 0;
          const height = Math.round((value / KRA_SCALE) * CHART_HEIGHT);
          return (
            <Stack key={month} role="listitem" aria-label={describe(month, row)}
              sx={{ alignItems: "center", minWidth: 36, flex: 1 }}>
              <Typography variant="caption" sx={{ minHeight: 18 }}>
                {shown ? points(row.final_total) : row ? "Pending" : ""}
              </Typography>
              <Box sx={{ height: CHART_HEIGHT, display: "flex", alignItems: "flex-end", width: "100%" }}>
                {shown && (
                  <Box data-testid={`bar-${month}`} data-state={row.state}
                    sx={{
                      width: "100%", height, borderRadius: "4px 4px 0 0",
                      bgcolor: row.state === "FINALIZED" ? "primary.main" : "transparent",
                      border: row.state === "PROVISIONAL" ? "2px dashed" : "none",
                      borderColor: "primary.main",
                    }} />
                )}
              </Box>
              <Typography variant="caption" color="text.secondary">{monthShort(month)}</Typography>
            </Stack>
          );
        })}
      </Stack>
      <Typography variant="caption" color="text.secondary">
        Solid: finalized. Dashed: provisional (month in progress). Scale: 0–{KRA_SCALE} points.
      </Typography>
    </Paper>
  );
}

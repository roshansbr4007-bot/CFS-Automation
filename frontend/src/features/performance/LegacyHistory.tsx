import { Paper, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";

import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { useMyLegacy } from "./api";
import { monthName } from "./labels";

/** My finalized results from before the KRA points system, on their own 0–100 scale. They are
 * kept apart from the KRA trend and annual figures (different scale). Hidden when there are none. */
export function LegacyHistory({ employee }: { employee: number }) {
  const legacy = useMyLegacy(employee);
  if (legacy.isError) return <ApiErrorAlert error={legacy.error} />;
  const rows = legacy.data ?? [];
  if (rows.length === 0) return null;
  return (
    <Paper sx={{ p: 2 }}>
      <Typography variant="h6" component="h3">Earlier results (0–100 scale)</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Finalized months scored under the earlier system. Not part of the KRA points above.
      </Typography>
      <Table size="small" aria-label="Earlier results">
        <TableHead>
          <TableRow><TableCell>Month</TableCell><TableCell>Overall score</TableCell><TableCell>Band</TableCell></TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id}>
              <TableCell>{monthName(row.month)} {row.year}</TableCell>
              <TableCell>{row.overall_score ?? "—"}</TableCell>
              <TableCell>{row.performance_band || "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );
}

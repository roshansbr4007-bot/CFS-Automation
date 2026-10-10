import { Alert, Box, Button, LinearProgress, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { useNavigate, useParams } from "react-router-dom";

import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DateTimeText } from "../../components/DateTimeText";
import { useKraMonth } from "./api";
import { STATUS_LABEL, monthName, points } from "./labels";

/** One KRA month for HR / Admin, read-only (the 7.3 review actions have no screen yet). */
export function KraMonthDetailPage() {
  const navigate = useNavigate();
  const id = Number(useParams().id);
  const month = useKraMonth(id);
  if (!Number.isInteger(id) || id <= 0) return <Alert severity="error">This KRA month does not exist.</Alert>;
  if (month.isPending) return <LinearProgress />;
  if (month.isError) return <ApiErrorAlert error={month.error} />;
  const m = month.data;
  const title = `${monthName(m.month)} ${m.year}`;
  return (
    <Stack spacing={2}>
      <Box>
        <Button onClick={() => navigate("/performance/months")}>Back to KRA months</Button>
        <Typography variant="h2" component="h1">KRA month · {title}</Typography>
        <Typography color="text.secondary">
          {STATUS_LABEL[m.status] ?? m.status}{m.provisional ? " (provisional)" : ""} · reopened {m.reopen_count} time(s)
          {m.finalized_at && <> · finalized <DateTimeText value={m.finalized_at} /></>}
        </Typography>
      </Box>
      <Paper sx={{ p: 2 }} aria-label="Totals">
        <Typography>
          Final <strong>{points(m.final_total)}</strong> of {points(m.max_points_applicable)} applicable points · automatic {points(m.auto_total)}
          {" "}· adjustments {points(m.adjustment_total)} · deductions {points(m.deduction_total)} · band {m.band || "—"}
          {m.band_ceiling ? ` (ceiling: ${m.band_ceiling})` : ""}
        </Typography>
      </Paper>
      {m.blockers.length > 0 && (
        <Alert severity="warning">
          Open before finalizing: {m.blockers.map((b) => b.message).join(" ")}
        </Alert>
      )}
      <Table size="small" aria-label="KPI points">
        <TableHead>
          <TableRow><TableCell>KPI</TableCell><TableCell>Max</TableCell><TableCell>Automatic</TableCell><TableCell>Adjustment</TableCell><TableCell>Deduction</TableCell><TableCell>Final</TableCell></TableRow>
        </TableHead>
        <TableBody>
          {m.kpis.map((k) => (
            <TableRow key={k.kpi_id}>
              <TableCell>{k.name}{k.not_applicable ? " (N/A)" : ""}</TableCell>
              <TableCell>{points(k.weight)}</TableCell>
              <TableCell>{points(k.auto_points)}</TableCell>
              <TableCell>{points(k.adjustment_points)}</TableCell>
              <TableCell>{points(k.deduction_points)}</TableCell>
              <TableCell>{points(k.final_points)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Typography variant="h6" component="h2">Deduction records</Typography>
      {m.deduction_applications.length === 0 ? (
        <Typography color="text.secondary">No deductions recorded.</Typography>
      ) : (
        <Table size="small" aria-label="Deduction records">
          <TableHead>
            <TableRow><TableCell>Rule</TableCell><TableCell>Scope</TableCell><TableCell>Percentage / ceiling</TableCell><TableCell>Evidence</TableCell><TableCell>Reason</TableCell><TableCell>Status</TableCell></TableRow>
          </TableHead>
          <TableBody>
            {m.deduction_applications.map((a) => (
              <TableRow key={a.id}>
                <TableCell>{a.rule_name}</TableCell>
                <TableCell>{a.scope}</TableCell>
                <TableCell>{a.percent ? `${points(a.percent)}%` : a.ceiling_band || "—"}</TableCell>
                <TableCell>{a.evidence || "—"}</TableCell>
                <TableCell>{a.reason}</TableCell>
                <TableCell>{a.reverses ? "Reversal" : a.reversed_by ? "Reversed" : a.active ? "Active" : "Not counted"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {m.deduction_lines.length > 0 && (
        <Table size="small" aria-label="Points taken per rule">
          <TableHead>
            <TableRow><TableCell>Rule</TableCell><TableCell>Applies to</TableCell><TableCell>Points</TableCell><TableCell>Counted</TableCell></TableRow>
          </TableHead>
          <TableBody>
            {m.deduction_lines.map((line, index) => (
              <TableRow key={index}>
                <TableCell>{line.rule_name}</TableCell>
                <TableCell>{[line.kpi, line.component].filter(Boolean).join(" · ") || "Whole month"}</TableCell>
                <TableCell>{line.ceiling_band ? `Band ceiling: ${line.ceiling_band}` : points(line.points)}</TableCell>
                <TableCell>{line.effective ? "Yes" : "No"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Stack>
  );
}

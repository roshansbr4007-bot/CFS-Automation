import {
  Alert, Box, Button, Chip, LinearProgress, MenuItem, Paper, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { Fragment, useState } from "react";

import type { KraMyHistory, KraMyMonth, KraMyMonthRow } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { useMyAnnual, useMyHistory, useMyMonth } from "./api";
import { LegacyHistory } from "./LegacyHistory";
import { STATE_LABEL, monthName, points } from "./labels";
import { TrendBars } from "./TrendBars";

/** Employee Home (Phase 7.4): my own KRA results only. The backend decides what is shown for each
 * month (provisional / under review / finalized) and never sends HR's reasons or evidence. */
export function MyPerformance() {
  const history = useMyHistory();
  if (history.isPending) return <LinearProgress aria-label="Loading my performance" />;
  if (history.isError) {
    return (
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <Box sx={{ flex: 1 }}><ApiErrorAlert error={history.error} /></Box>
        <Button onClick={() => void history.refetch()}>Retry</Button>
      </Stack>
    );
  }
  if (history.data === null) {
    return <Alert severity="info">No employee record is linked to your login, so there are no performance results to show.</Alert>;
  }
  return <MyPerformanceBody data={history.data} />;
}

function MyPerformanceBody({ data }: { data: KraMyHistory }) {
  const { employee, current, months } = data;
  const currentRow = months.find((m) => m.year === current.year && m.month === current.month);
  const years = Array.from(new Set([current.year, ...months.map((m) => m.year)])).sort((a, b) => b - a);
  const [year, setYear] = useState(current.year);
  const [selected, setSelected] = useState<number | null>(currentRow?.id ?? months[0]?.id ?? null);

  return (
    <Stack spacing={2}>
      <Typography variant="h5" component="h2">My performance</Typography>
      <CurrentMonth current={current} row={currentRow} />
      {months.length === 0 ? (
        <Alert severity="info">No KRA results yet.</Alert>
      ) : (
        <>
          <TextField select label="Year" size="small" value={year} sx={{ width: 160 }}
            onChange={(event) => setYear(Number(event.target.value))}>
            {years.map((y) => <MenuItem key={y} value={y}>{y}</MenuItem>)}
          </TextField>
          <AnnualCard year={year} />
          <TrendBars year={year} months={months} />
          <History months={months} selected={selected} onSelect={setSelected} />
          {selected !== null && <MonthDetail id={selected} />}
        </>
      )}
      <LegacyHistory employee={employee.id} />
    </Stack>
  );
}

function StateChip({ state }: { state: KraMyMonthRow["state"] }) {
  const color = state === "FINALIZED" ? "success" : state === "PROVISIONAL" ? "info" : "default";
  return <Chip size="small" label={STATE_LABEL[state]} color={color} variant="outlined" />;
}

function CurrentMonth({ current, row }: { current: KraMyHistory["current"]; row?: KraMyMonthRow }) {
  const title = `${monthName(current.month)} ${current.year}`;
  return (
    <Paper sx={{ p: 2 }} aria-label="Current month">
      <Typography variant="overline">Current month · {title}</Typography>
      {!row ? (
        <Typography>Not calculated yet.</Typography>
      ) : row.state === "PENDING_REVIEW" ? (
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <StateChip state={row.state} />
        </Stack>
      ) : (
        <Stack direction="row" spacing={2} sx={{ alignItems: "baseline", flexWrap: "wrap" }}>
          <Typography variant="h4" component="p">{points(row.final_total)}</Typography>
          <Typography color="text.secondary">of {points(row.max_points_applicable)} applicable points</Typography>
          <Typography>{row.band}{row.state === "PROVISIONAL" ? " (provisional)" : ""}</Typography>
          <StateChip state={row.state} />
        </Stack>
      )}
    </Paper>
  );
}

function AnnualCard({ year }: { year: number }) {
  const annual = useMyAnnual(year, true);
  if (annual.isError) return <ApiErrorAlert error={annual.error} />;
  const data = annual.data;
  return (
    <Paper sx={{ p: 2 }} aria-label={`Annual ${year}`}>
      <Typography variant="h6" component="h3">Annual {year}</Typography>
      {!data ? <LinearProgress /> : data.applicable_months === 0 ? (
        <Typography color="text.secondary">No finalized months in {year} yet.</Typography>
      ) : (
        <Stack direction="row" spacing={3} sx={{ flexWrap: "wrap" }}>
          <Typography>Total: <strong>{points(data.annual_total)}</strong> of {points(data.maximum_total)}</Typography>
          <Typography>Average: <strong>{points(data.annual_average)}</strong></Typography>
          <Typography>Finalized months counted: {data.applicable_months}</Typography>
        </Stack>
      )}
      <Typography variant="caption" color="text.secondary">
        Only finalized months count. Months without a result or not finalized yet are left out, never counted as 0.
      </Typography>
    </Paper>
  );
}

function History({ months, selected, onSelect }: {
  months: KraMyMonthRow[]; selected: number | null; onSelect: (id: number) => void;
}) {
  return (
    <Paper>
      <Table size="small" aria-label="My KRA months">
        <TableHead>
          <TableRow><TableCell>Month</TableCell><TableCell>State</TableCell><TableCell>Final score</TableCell><TableCell>Band</TableCell><TableCell /></TableRow>
        </TableHead>
        <TableBody>
          {months.map((row) => (
            <TableRow key={row.id} selected={row.id === selected}>
              <TableCell>{monthName(row.month)} {row.year}</TableCell>
              <TableCell><StateChip state={row.state} /></TableCell>
              <TableCell>{row.final_total === null ? "—" : `${points(row.final_total)} / ${points(row.max_points_applicable)}`}</TableCell>
              <TableCell>{row.band ?? "—"}</TableCell>
              <TableCell><Button size="small" onClick={() => onSelect(row.id)}>View</Button></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );
}

function MonthDetail({ id }: { id: number }) {
  const month = useMyMonth(id);
  if (month.isPending) return <LinearProgress />;
  if (month.isError) return <ApiErrorAlert error={month.error} />;
  return <MonthDetailBody month={month.data} />;
}

function MonthDetailBody({ month }: { month: KraMyMonth }) {
  const title = `${monthName(month.month)} ${month.year}`;
  if (month.state === "PENDING_REVIEW") {
    return (
      <Paper sx={{ p: 2 }} aria-label={`Details ${title}`}>
        <Typography variant="h6" component="h3">{title}</Typography>
        <Alert severity="info">Under review – score pending. The score is shown once HR has finalized it.</Alert>
      </Paper>
    );
  }
  return (
    <Paper sx={{ p: 2 }} aria-label={`Details ${title}`}>
      <Stack spacing={1}>
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Typography variant="h6" component="h3">{title}</Typography>
          <StateChip state={month.state} />
        </Stack>
        {month.state === "PROVISIONAL" && (
          <Alert severity="info">Provisional: the month is still running, so these figures can change.</Alert>
        )}
        <Typography>
          Final score <strong>{points(month.final_total)}</strong> of {points(month.max_points_applicable)} applicable points
          (automatic score {points(month.auto_total)}) · {month.band}
        </Typography>
        <Table size="small" aria-label={`KPI scores ${title}`}>
          <TableHead>
            <TableRow><TableCell>KPI / component</TableCell><TableCell>Max</TableCell><TableCell>Achievement</TableCell><TableCell>Automatic</TableCell><TableCell>Final</TableCell></TableRow>
          </TableHead>
          <TableBody>
            {(month.kpis ?? []).map((kpi) => (
              <Fragment key={kpi.name}>
                <TableRow>
                  <TableCell><strong>{kpi.name}</strong></TableCell>
                  <TableCell>{points(kpi.weight)}</TableCell>
                  {kpi.not_applicable ? (
                    <TableCell colSpan={3}>{kpi.na_label}</TableCell>
                  ) : (
                    <>
                      <TableCell>{kpi.achievement_pct === null ? "—" : `${points(kpi.achievement_pct)}%`}</TableCell>
                      <TableCell>{points(kpi.auto_points)}</TableCell>
                      <TableCell>{points(kpi.final_points)}</TableCell>
                    </>
                  )}
                </TableRow>
                {kpi.components.map((component, index) => (
                  <TableRow key={`${kpi.name}-${index}`}>
                    <TableCell sx={{ pl: 4 }}>{component.label}</TableCell>
                    <TableCell />
                    {component.applicable ? (
                      <TableCell colSpan={3}>
                        {component.achievement_pct === null ? "—" : `${points(component.achievement_pct)}%`}
                        {" · "}on time {component.on_time_count}, late {component.late_count}, overdue {component.overdue_count}
                      </TableCell>
                    ) : (
                      <TableCell colSpan={3}>{component.na_label}</TableCell>
                    )}
                  </TableRow>
                ))}
              </Fragment>
            ))}
          </TableBody>
        </Table>
        {(month.deductions ?? []).length > 0 && (
          <Table size="small" aria-label={`Deductions ${title}`}>
            <TableHead>
              <TableRow><TableCell>Deduction</TableCell><TableCell>Applies to</TableCell><TableCell>Points</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {(month.deductions ?? []).map((line, index) => (
                <TableRow key={index}>
                  <TableCell>{line.rule}</TableCell>
                  <TableCell>{[line.kpi, line.component].filter(Boolean).join(" · ") || "Whole month"}</TableCell>
                  <TableCell>{points(line.points)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Stack>
    </Paper>
  );
}

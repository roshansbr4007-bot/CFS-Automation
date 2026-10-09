import { Button, Chip, MenuItem, Paper, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import { departmentsApi, employeesApi, operationsApi } from "../../api/endpoints";
import { PERM, type AssignedTaskRow, type DailyActivity, type EmployeeWorkSummary, type MonitoredEmployee, type SlaState, type TeamOperationsSummary } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText, formatBusinessDate } from "../../components/DateTimeText";
import { ResultChip } from "../tasks/DailyActivities";
import { PRIORITY_LABEL, STATUS_LABEL } from "../tasks/labels";
import { SLA_LABEL, SlaStateChip } from "../tasks/SlaBadge";

type Mode = "today" | "yesterday" | "custom";

/** Calendar arithmetic on a YYYY-MM-DD business date (no timezone involved). */
export function shiftDate(day: string, days: number): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

const n = (value: number, danger = false) => danger && value > 0 ? <Chip size="small" color="error" label={value} /> : value;

/** Employee-wise view of daily activities and assigned tasks, kept separate.
 * Admin = Boss: the whole organisation, with a department filter.
 * Operations Manager: their own department only, through the team endpoints; the server forces the
 * department, so there is no department filter here. */
export function OperationsMonitorPage() {
  const { hasPerm } = useAuth();
  const team = !hasPerm(PERM.manageAllTasks);
  const [mode, setMode] = useState<Mode>("today");
  const [custom, setCustom] = useState("");
  const [serverToday, setServerToday] = useState<string | null>(null);
  const [department, setDepartment] = useState("");
  const [employee, setEmployee] = useState("");
  const [selected, setSelected] = useState<MonitoredEmployee | null>(null);

  const date = mode === "today" ? undefined : mode === "yesterday" ? (serverToday ? shiftDate(serverToday, -1) : undefined) : custom || undefined;
  const params = team ? { date, employee } : { date, department, employee };
  const { data, error, isFetching } = useQuery({
    queryKey: [team ? "operations-team-summary" : "operations-summary", params],
    queryFn: () => team ? operationsApi.teamSummary(params) : operationsApi.summary(params),
    placeholderData: keepPreviousData, refetchInterval: 60_000, enabled: mode !== "custom" || !!custom,
  });
  useEffect(() => { if (mode === "today" && data?.date) setServerToday(data.date); }, [mode, data?.date]);
  const { data: departments = [] } = useQuery({ queryKey: ["departments"], queryFn: departmentsApi.list, enabled: !team });
  const teamDepartment = team ? (data as TeamOperationsSummary | undefined)?.department ?? null : null;
  const { data: people } = useQuery({ queryKey: ["employees", { page: 1, is_active: true }], queryFn: () => employeesApi.list({ page: 1, is_active: true }) });

  const columns: Column<EmployeeWorkSummary>[] = [
    { key: "employee", header: "Employee", render: (r) => r.employee.full_name },
    { key: "department", header: "Department", render: (r) => r.employee.department.code },
    { key: "dt", header: "Daily total", render: (r) => r.daily_activity.total },
    { key: "dd", header: "Daily done", render: (r) => r.daily_activity.completed },
    { key: "dp", header: "Daily pending", render: (r) => r.daily_activity.pending },
    { key: "do", header: "Daily overdue", render: (r) => n(r.daily_activity.overdue, true) },
    { key: "tt", header: "Tasks total", render: (r) => r.assigned_tasks.total },
    { key: "td", header: "Tasks done", render: (r) => r.assigned_tasks.completed },
    { key: "tp", header: "Tasks pending", render: (r) => r.assigned_tasks.pending },
    { key: "to", header: "Tasks overdue", render: (r) => n(r.assigned_tasks.overdue, true) },
    { key: "view", header: "", render: (r) => <Button size="small" onClick={() => setSelected(r.employee)}>View</Button> },
  ];
  const rows = data?.employees ?? [];

  return (
    <Stack spacing={3}>
      <Typography variant="h2" component="h1">Operations monitor</Typography>
      {team && teamDepartment && <Typography>Your department: <strong>{teamDepartment.code} — {teamDepartment.name}</strong></Typography>}
      <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ alignItems: { md: "center" } }}>
        <ToggleButtonGroup exclusive size="small" value={mode} onChange={(_, v: Mode | null) => v && setMode(v)} aria-label="Date">
          <ToggleButton value="today">Today</ToggleButton>
          <ToggleButton value="yesterday" disabled={!serverToday}>Yesterday</ToggleButton>
          <ToggleButton value="custom">Custom date</ToggleButton>
        </ToggleButtonGroup>
        {mode === "custom" && <TextField size="small" label="Date" type="date" value={custom} onChange={(e) => setCustom(e.target.value)} InputLabelProps={{ shrink: true }} />}
        {!team && <TextField size="small" select label="Department" value={department} onChange={(e) => setDepartment(String(e.target.value))} sx={{ minWidth: 160 }}>
          <MenuItem value="">All departments</MenuItem>
          {departments.map((d) => <MenuItem key={d.id} value={String(d.id)}>{d.code}</MenuItem>)}
        </TextField>}
        <TextField size="small" select label="Employee" value={employee} onChange={(e) => setEmployee(String(e.target.value))} sx={{ minWidth: 180 }}>
          <MenuItem value="">All employees</MenuItem>
          {(people?.results ?? []).map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name}</MenuItem>)}
        </TextField>
      </Stack>
      {data && <Typography color="text.secondary">Showing {formatBusinessDate(data.date)}</Typography>}
      <ApiErrorAlert error={error} />
      <DataTable caption="Employee summary" columns={columns} rows={rows} getRowId={(r) => r.employee.id} loading={isFetching}
        total={rows.length} page={0} onPageChange={() => undefined} emptyMessage="No active employees match these filters." />
      {selected && data && <EmployeeDetail employee={selected} date={data.date} team={team} onClose={() => setSelected(null)} />}
    </Stack>
  );
}

function EmployeeDetail({ employee, date, team, onClose }: { employee: MonitoredEmployee; date: string; team: boolean; onClose: () => void }) {
  const [dailyStatus, setDailyStatus] = useState("");
  const [slaState, setSlaState] = useState("");
  const [taskStatus, setTaskStatus] = useState("");
  const daily = useQuery({
    queryKey: ["operations-daily", team, employee.id, date, dailyStatus, slaState],
    queryFn: () => (team ? operationsApi.teamDailyActivities : operationsApi.dailyActivities)(employee.id, { date, status: dailyStatus, sla_state: slaState }),
  });
  const tasks = useQuery({
    queryKey: ["operations-tasks", team, employee.id, date, taskStatus],
    queryFn: () => (team ? operationsApi.teamAssignedTasks : operationsApi.assignedTasks)(employee.id, { date, status: taskStatus }),
  });
  const dailyColumns: Column<DailyActivity>[] = [
    { key: "activity", header: "Activity", render: (a) => a.responsibility?.name ?? a.title },
    { key: "start", header: "Scheduled start", render: (a) => <DateTimeText value={a.scheduled_start} /> },
    { key: "deadline", header: "Deadline", render: (a) => a.deadline ? <DateTimeText value={a.deadline} /> : "No deadline" },
    { key: "status", header: "Status", render: (a) => STATUS_LABEL[a.status] },
    { key: "sla", header: "SLA", render: (a) => <SlaStateChip state={a.sla_state} /> },
    { key: "completed", header: "Completed at", render: (a) => <DateTimeText value={a.completed_at} /> },
    { key: "result", header: "On time / late", render: (a) => a.completion_result ? <ResultChip result={a.completion_result} /> : a.is_overdue ? <Chip size="small" color="error" label="Overdue" /> : "—" },
    { key: "open", header: "", render: (a) => <Button size="small" component={RouterLink} to={`/tasks/${a.task_id}`}>Open</Button> },
  ];
  const taskColumns: Column<AssignedTaskRow>[] = [
    { key: "task", header: "Task", render: (t) => t.title },
    { key: "raised", header: "Raised by", render: (t) => t.raised_by.full_name || t.raised_by.email },
    { key: "classification", header: "Department / category", render: (t) => `${t.department.code} / ${t.category?.name ?? "—"}` },
    { key: "assigned", header: "Assigned at", render: (t) => <DateTimeText value={t.assigned_at} /> },
    { key: "by", header: "Assigned by", render: (t) => t.assigned_by.full_name || t.assigned_by.email },
    { key: "due", header: "Due date", render: (t) => t.deadline ? <DateTimeText value={t.deadline} /> : "—" },
    { key: "priority", header: "Priority", render: (t) => PRIORITY_LABEL[t.priority] },
    { key: "status", header: "Status", render: (t) => STATUS_LABEL[t.status] },
    { key: "completed", header: "Completed at", render: (t) => <DateTimeText value={t.completed_at} /> },
    { key: "result", header: "On time / late", render: (t) => t.completion_result ? <ResultChip result={t.completion_result} /> : t.is_overdue ? <Chip size="small" color="error" label="Overdue" /> : "—" },
    { key: "open", header: "", render: (t) => <Button size="small" component={RouterLink} to={`/tasks/${t.task_id}`}>Open</Button> },
  ];
  const statusFilter = (label: string, value: string, set: (v: string) => void) => (
    <TextField size="small" select label={label} value={value} onChange={(e) => set(e.target.value)} sx={{ minWidth: 150 }}>
      <MenuItem value="">All</MenuItem>
      <MenuItem value="pending">Pending</MenuItem>
      <MenuItem value="completed">Completed</MenuItem>
      <MenuItem value="overdue">Overdue</MenuItem>
    </TextField>
  );
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={2}>
        <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
          <Typography variant="h3" component="h2">{employee.full_name} — {formatBusinessDate(date)}</Typography>
          <Button onClick={onClose}>Close</Button>
        </Stack>
        <Typography variant="h4" component="h3">Daily activities</Typography>
        <Stack direction="row" spacing={2}>
          {statusFilter("Activity status", dailyStatus, setDailyStatus)}
          <TextField size="small" select label="SLA state" value={slaState} onChange={(e) => setSlaState(e.target.value)} sx={{ minWidth: 150 }}>
            <MenuItem value="">All</MenuItem>
            {(Object.keys(SLA_LABEL) as SlaState[]).map((s) => <MenuItem key={s} value={s}>{SLA_LABEL[s]}</MenuItem>)}
          </TextField>
        </Stack>
        <ApiErrorAlert error={daily.error} />
        <DataTable caption="Daily activities" columns={dailyColumns} rows={daily.data?.activities ?? []} getRowId={(a) => a.task_id}
          loading={daily.isFetching} total={daily.data?.activities.length ?? 0} page={0} onPageChange={() => undefined}
          emptyMessage="No daily activities for this date." />
        <Typography variant="h4" component="h3">Assigned tasks</Typography>
        {statusFilter("Task status", taskStatus, setTaskStatus)}
        <ApiErrorAlert error={tasks.error} />
        <DataTable caption="Assigned tasks" columns={taskColumns} rows={tasks.data?.tasks ?? []} getRowId={(t) => t.task_id}
          loading={tasks.isFetching} total={tasks.data?.tasks.length ?? 0} page={0} onPageChange={() => undefined}
          emptyMessage="No assigned tasks for this date." />
      </Stack>
    </Paper>
  );
}

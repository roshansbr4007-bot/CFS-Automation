import { Alert, Box, Button, Chip, MenuItem, Paper, Stack, TextField, Typography } from "@mui/material";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import { commandCenterApi, departmentsApi, employeesApi } from "../../api/endpoints";
import {
  TASK_STATUSES, type AttentionState, type CommandCenterEmployee, type CommandCenterEmployeeDetail, type CommandCenterFilters,
  type HealthCheck, type HealthStatus, type MonitoredEmployee, type RecentEvent, type SchedulerJob, type SlaAttentionItem,
  type SlaAttention, type SlaCounts, type SlaState, type WorkCounts,
} from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { formatRemaining } from "../../components/Countdown";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText, formatBusinessDate } from "../../components/DateTimeText";
import { ScheduledCell } from "../tasks/DailyActivities";
import { PRIORITY_LABEL, STATUS_LABEL } from "../tasks/labels";
import { SLA_LABEL, SlaStateChip } from "../tasks/SlaBadge";

const STATUS: Record<HealthStatus, { label: string; color: "success" | "error" | "default" | "warning" }> = {
  HEALTHY: { label: "Healthy", color: "success" },
  FAILED: { label: "Failed", color: "error" },
  NEVER_RUN: { label: "Never run", color: "warning" },
  UNKNOWN: { label: "Unknown", color: "default" },
};
const CHECK_NAME: Record<string, string> = { api: "API", database: "Database", redis: "Redis", celery_workers: "Celery workers" };

export const QUICK_ACTIONS = [
  { label: "Operations monitor", to: "/admin/operations" },
  { label: "Tasks", to: "/tasks" },
  { label: "Responsibilities", to: "/responsibilities" },
  { label: "Schedules", to: "/schedules" },
  { label: "Employees", to: "/employees" },
  { label: "Audit log", to: "/admin/audit" },
  { label: "Company calendar", to: "/admin/calendar" },
];

function StatusChip({ status }: { status: HealthStatus }) {
  return <Chip size="small" color={STATUS[status].color} label={STATUS[status].label} />;
}

function Stat({ label, value, danger = false }: { label: string; value: number; danger?: boolean }) {
  return (
    <Paper variant="outlined" sx={{ p: 1.5, minWidth: 120 }}>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Typography variant="h3" component="p" color={danger && value > 0 ? "error" : undefined}>{value}</Typography>
    </Paper>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Box component="section" aria-label={title}>
      <Typography variant="h3" component="h2" sx={{ mb: 1.5 }}>{title}</Typography>
      {children}
    </Box>
  );
}

function workStats(prefix: string, c: WorkCounts) {
  return [
    <Stat key="t" label={`${prefix} total`} value={c.total} />,
    <Stat key="c" label={`${prefix} completed`} value={c.completed} />,
    <Stat key="p" label={`${prefix} pending`} value={c.pending} />,
    <Stat key="o" label={`${prefix} overdue`} value={c.overdue} danger />,
    <Stat key="l" label={`${prefix} completed late`} value={c.completed_late} danger />,
  ];
}

function slaStats(c: SlaCounts) {
  return (
    <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1.5 }}>
      <Stat label="On track" value={c.on_track} />
      <Stat label="Warning" value={c.warning} />
      <Stat label="Critical" value={c.critical} danger />
      <Stat label="Overdue" value={c.overdue} danger />
      <Stat label="Completed on time" value={c.completed_on_time} />
      <Stat label="Completed late" value={c.completed_late} danger />
    </Stack>
  );
}

const ATTENTION: Record<AttentionState, { label: string; color: "error" | "warning" | "default" | "success" | "info" }> = {
  OVERDUE: { label: "Overdue", color: "error" },
  CRITICAL: { label: "Critical", color: "error" },
  WARNING: { label: "Warning", color: "warning" },
  BLOCKED: { label: "Blocked", color: "info" },
  ON_TRACK: { label: "On track", color: "success" },
  NO_ACTIVE_WORK: { label: "No active work", color: "default" },
};
const SOURCE_LABEL = { SCHEDULED: "Daily activity", MANUAL: "Assigned task" } as const;
const POLL_MS = 60_000;

function AttentionChips({ states }: { states: AttentionState[] }) {
  return <Stack direction="row" sx={{ gap: 0.5, flexWrap: "wrap" }}>{states.map((s) => <Chip key={s} size="small" color={ATTENTION[s].color} label={ATTENTION[s].label} />)}</Stack>;
}

function Remaining({ seconds }: { seconds: number | null }) {
  if (seconds === null) return <>—</>;
  return <>{seconds <= 0 ? "Overdue" : formatRemaining(seconds)}</>;
}

/** Retry-able error for one section; other sections keep working. */
function SectionError({ error, onRetry, what }: { error: unknown; onRetry: () => void; what: string }) {
  if (!error) return null;
  return <Alert severity="error" role="alert" action={<Button color="inherit" size="small" onClick={onRetry}>Retry</Button>}>Unable to load {what}.</Alert>;
}

/** Admin (Boss) Command Center: a read-only view of the whole platform. Every number comes from the
 * backend; system health is checked live but separately, so a slow check never blocks the page.
 * Phase 6B: server-side filters, an overview, SLA attention and a read-only employee drill-down. */
export function CommandCenterPage() {
  const [filters, setFilters] = useState<CommandCenterFilters>({});
  const [selected, setSelected] = useState<MonitoredEmployee | null>(null);
  const set = (key: keyof CommandCenterFilters) => (value: string) => setFilters((f) => ({ ...f, [key]: value }));
  const summary = useQuery({ queryKey: ["command-center", "summary", filters], queryFn: () => commandCenterApi.summary(filters), refetchInterval: POLL_MS });
  const attention = useQuery({ queryKey: ["command-center", "sla-attention", filters], queryFn: () => commandCenterApi.slaAttention(filters), refetchInterval: POLL_MS });
  const health = useQuery({ queryKey: ["command-center", "health"], queryFn: commandCenterApi.health });
  const { data: departments = [] } = useQuery({ queryKey: ["departments"], queryFn: departmentsApi.list });
  const { data: people } = useQuery({ queryKey: ["employees", { page: 1 }], queryFn: () => employeesApi.list({ page: 1 }) });
  const refresh = () => { void summary.refetch(); void health.refetch(); void attention.refetch(); };
  const data = summary.data;

  const healthColumns: Column<HealthCheck>[] = [
    { key: "name", header: "Component", render: (c) => CHECK_NAME[c.name] ?? c.name },
    { key: "status", header: "Status", render: (c) => <StatusChip status={c.status} /> },
    { key: "detail", header: "Detail", render: (c) => c.detail },
  ];
  const schedulerColumns: Column<SchedulerJob>[] = [
    { key: "name", header: "Job", render: (j) => j.name },
    { key: "status", header: "Status", render: (j) => <StatusChip status={j.status} /> },
    { key: "success", header: "Last success", render: (j) => <DateTimeText value={j.last_success_at} /> },
    { key: "started", header: "Last started", render: (j) => <DateTimeText value={j.last_started_at} /> },
    { key: "result", header: "Last result", render: (j) => Object.entries(j.last_summary).map(([k, v]) => `${k}: ${v}`).join(", ") || "—" },
    { key: "detail", header: "Detail", render: (j) => j.detail },
  ];
  const employeeColumns: Column<CommandCenterEmployee>[] = [
    { key: "employee", header: "Employee", render: (r) => r.employee.full_name },
    { key: "department", header: "Department", render: (r) => r.employee.department.code },
    { key: "dt", header: "Scheduled today", render: (r) => r.daily_activity.total },
    { key: "dd", header: "Completed", render: (r) => r.daily_activity.completed },
    { key: "dp", header: "Pending", render: (r) => r.daily_activity.pending },
    { key: "do", header: "Overdue", render: (r) => r.daily_activity.overdue },
    { key: "tt", header: "Manual tasks", render: (r) => r.assigned_tasks.total },
    { key: "tp", header: "Manual pending", render: (r) => r.assigned_tasks.pending },
    { key: "to", header: "Manual overdue", render: (r) => r.assigned_tasks.overdue },
    { key: "attention", header: "Current attention", render: (r) => <AttentionChips states={r.attention} /> },
    { key: "view", header: "", render: (r) => <Button size="small" onClick={() => setSelected(r.employee)}>View</Button> },
  ];
  const eventColumns: Column<RecentEvent>[] = [
    { key: "time", header: "When", render: (e) => <DateTimeText value={e.occurred_at} /> },
    { key: "action", header: "Action", render: (e) => e.action },
    { key: "entity", header: "Entity", render: (e) => `${e.entity_type} #${e.entity_id}` },
    { key: "actor", header: "By", render: (e) => e.actor ? (e.actor.full_name || e.actor.email) : "System" },
  ];

  return (
    <Stack spacing={4}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Box>
          <Typography variant="h2" component="h1">Command center</Typography>
          {data && <Typography color="text.secondary">{formatBusinessDate(data.date)} · as of <DateTimeText value={data.server_time} /></Typography>}
        </Box>
        <Button variant="outlined" onClick={refresh} disabled={summary.isFetching || health.isFetching}>Refresh</Button>
      </Stack>

      <Section title="Filters">
        <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1.5 }}>
          <TextField size="small" label="Date" type="date" value={filters.date ?? data?.date ?? ""} onChange={(e) => set("date")(e.target.value)} InputLabelProps={{ shrink: true }} />
          <TextField size="small" select label="Department" value={filters.department ?? ""} onChange={(e) => set("department")(String(e.target.value))} sx={{ minWidth: 150 }}>
            <MenuItem value="">All departments</MenuItem>
            {departments.map((d) => <MenuItem key={d.id} value={String(d.id)}>{d.code}</MenuItem>)}
          </TextField>
          <TextField size="small" select label="Employee" value={filters.employee ?? ""} onChange={(e) => set("employee")(String(e.target.value))} sx={{ minWidth: 170 }}>
            <MenuItem value="">All employees</MenuItem>
            {(people?.results ?? []).map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name}</MenuItem>)}
          </TextField>
          <TextField size="small" select label="Source" value={filters.source ?? ""} onChange={(e) => set("source")(e.target.value)} sx={{ minWidth: 160 }}>
            <MenuItem value="">All work</MenuItem>
            <MenuItem value="SCHEDULED">Daily activities</MenuItem>
            <MenuItem value="MANUAL">Assigned tasks</MenuItem>
          </TextField>
          <TextField size="small" select label="Status" value={filters.status ?? ""} onChange={(e) => set("status")(e.target.value)} sx={{ minWidth: 140 }}>
            <MenuItem value="">All statuses</MenuItem>
            {TASK_STATUSES.map((s) => <MenuItem key={s} value={s}>{STATUS_LABEL[s]}</MenuItem>)}
          </TextField>
          <TextField size="small" select label="SLA state" value={filters.sla_state ?? ""} onChange={(e) => set("sla_state")(e.target.value)} sx={{ minWidth: 140 }}>
            <MenuItem value="">All SLA states</MenuItem>
            {(Object.keys(SLA_LABEL) as SlaState[]).map((s) => <MenuItem key={s} value={s}>{SLA_LABEL[s]}</MenuItem>)}
          </TextField>
          <Button size="small" onClick={() => { setFilters({}); setSelected(null); }}>Clear filters</Button>
        </Stack>
      </Section>

      <Section title="Quick actions">
        <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1 }}>
          {QUICK_ACTIONS.map((a) => <Button key={a.to} size="small" variant="outlined" component={RouterLink} to={a.to}>{a.label}</Button>)}
        </Stack>
      </Section>

      <Section title="System health">
        <ApiErrorAlert error={health.error} />
        {health.isPending ? <Typography>Checking system health…</Typography> : health.data && (
          <>
            <Typography sx={{ mb: 1 }}>Overall: <StatusChip status={health.data.overall} /></Typography>
            <DataTable caption="System health" columns={healthColumns} rows={health.data.checks} getRowId={(c) => c.name}
              loading={health.isFetching} total={health.data.checks.length} page={0} onPageChange={() => undefined} emptyMessage="No checks." />
          </>
        )}
      </Section>

      <ApiErrorAlert error={summary.error} />
      {summary.error ? <Button size="small" onClick={() => void summary.refetch()}>Retry</Button> : null}
      {summary.isPending && <Typography>Loading command center…</Typography>}
      {data && (
        <>
          <Section title="Today overview">
            <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1.5 }}>
              <Stat label="Active employees" value={data.overview.employees.total_active} />
              <Stat label="Employees with work" value={data.overview.employees.with_work} />
              <Stat label="Daily activities" value={data.overview.daily_activities.scheduled} />
              <Stat label="Assigned tasks active" value={data.overview.assigned_tasks.active} />
              <Stat label="Overdue work" value={data.overview.daily_activities.overdue + data.overview.assigned_tasks.overdue} danger />
              <Stat label="Critical SLA" value={data.overview.sla.critical} danger />
            </Stack>
          </Section>
          <Section title="Scheduler health">
            <DataTable caption="Scheduler health" columns={schedulerColumns} rows={data.scheduler} getRowId={(j) => j.job}
              loading={summary.isFetching} total={data.scheduler.length} page={0} onPageChange={() => undefined} emptyMessage="No scheduled jobs." />
            <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1.5, mt: 1.5 }}>
              <Stat label="Generated today" value={data.todays_occurrences.generated} />
              <Stat label="Skipped today" value={data.todays_occurrences.skipped} danger />
              <Stat label="Missed today" value={data.todays_occurrences.missed} danger />
              <Stat label="Failed today" value={data.todays_occurrences.failed} danger />
            </Stack>
          </Section>

          <Section title="Today's operations">
            <Typography variant="h4" component="h3" sx={{ mb: 1 }}>Daily activity monitor</Typography>
            <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1.5 }}>
              {workStats("Scheduled", data.operations.daily_activity)}
              <Stat label="Scheduled in progress" value={data.overview.daily_activities.in_progress} />
              <Stat label="Scheduled blocked" value={data.overview.daily_activities.blocked} />
            </Stack>
            <Typography variant="h4" component="h3" sx={{ my: 1 }}>Assigned task monitor</Typography>
            <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1.5 }}>
              {workStats("Manual", data.operations.assigned_tasks)}
              <Stat label="Manual active" value={data.overview.assigned_tasks.active} />
              <Stat label="Manual in progress" value={data.overview.assigned_tasks.in_progress} />
              <Stat label="Manual blocked" value={data.overview.assigned_tasks.blocked} />
            </Stack>
          </Section>

          <Section title="SLA snapshot">
            <Typography variant="h4" component="h3" sx={{ mb: 1 }}>Daily responsibilities</Typography>
            {slaStats(data.sla.daily_activity)}
            <Typography variant="h4" component="h3" sx={{ my: 1 }}>Manual tasks</Typography>
            {slaStats(data.sla.assigned_tasks)}
          </Section>

          <Section title="Employees">
            <DataTable caption="Employee summary" columns={employeeColumns} rows={data.employees} getRowId={(r) => r.employee.id}
              loading={summary.isFetching} total={data.employees.length} page={0} onPageChange={() => undefined} emptyMessage="No active employees." />
            <Button size="small" component={RouterLink} to="/admin/operations" sx={{ mt: 1 }}>Open the Operations monitor for details</Button>
            {selected && <EmployeeDrillDown employee={selected} filters={filters} onClose={() => setSelected(null)} />}
          </Section>

          <SlaAttentionPanel query={attention} />

          <Section title="Recent events">
            <DataTable caption="Recent events" columns={eventColumns} rows={data.recent_events} getRowId={(e) => e.id}
              loading={summary.isFetching} total={data.recent_events.length} page={0} onPageChange={() => undefined} emptyMessage="No events yet." />
          </Section>
        </>
      )}
    </Stack>
  );
}

function attentionColumns(): Column<SlaAttentionItem>[] {
  return [
    { key: "employee", header: "Employee", render: (i) => i.employee.full_name },
    { key: "work", header: "Task / activity", render: (i) => i.title },
    { key: "source", header: "Source", render: (i) => SOURCE_LABEL[i.source] },
    { key: "deadline", header: "Deadline", render: (i) => <DateTimeText value={i.deadline} /> },
    { key: "state", header: "SLA state", render: (i) => <SlaStateChip state={i.sla_state} /> },
    { key: "remaining", header: "Remaining", render: (i) => <Remaining seconds={i.remaining_seconds} /> },
  ];
}

function SlaAttentionPanel({ query }: { query: UseQueryResult<SlaAttention> }) {
  const groups = [
    { key: "critical", title: "Critical" },
    { key: "warning", title: "Warning" },
    { key: "overdue", title: "Overdue" },
  ] as const;
  return (
    <Section title="SLA attention">
      <SectionError error={query.error} onRetry={() => void query.refetch()} what="SLA attention" />
      {query.isPending && <Typography>Loading SLA attention…</Typography>}
      {query.data && groups.map((g) => (
        <Box key={g.key} sx={{ mb: 2 }}>
          <Typography variant="h4" component="h3" sx={{ mb: 1 }}>{g.title} ({query.data[g.key].length})</Typography>
          <DataTable caption={`SLA attention: ${g.title}`} columns={attentionColumns()} rows={query.data[g.key]} getRowId={(i) => i.task_id}
            loading={query.isFetching} total={query.data[g.key].length} page={0} onPageChange={() => undefined}
            emptyMessage="No active items for the selected filters." />
        </Box>
      ))}
    </Section>
  );
}

/** Read-only drill-down: one employee's daily activities and assigned tasks, kept apart. */
function EmployeeDrillDown({ employee, filters, onClose }: { employee: MonitoredEmployee; filters: CommandCenterFilters; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ["command-center", "employee", employee.id, filters],
    queryFn: () => commandCenterApi.employee(employee.id, filters),
  });
  const dailyColumns: Column<CommandCenterEmployeeDetail["daily_activities"][number]>[] = [
    { key: "activity", header: "Activity", render: (a) => a.responsibility?.name ?? a.title },
    { key: "start", header: "Scheduled", render: (a) => <ScheduledCell activity={a} /> },
    { key: "deadline", header: "Deadline", render: (a) => a.deadline ? <DateTimeText value={a.deadline} /> : "No deadline" },
    { key: "status", header: "Status", render: (a) => STATUS_LABEL[a.status] },
    { key: "sla", header: "SLA state", render: (a) => <SlaStateChip state={a.sla_state} /> },
    { key: "completed", header: "Completed at", render: (a) => <DateTimeText value={a.completed_at} /> },
    { key: "assignee", header: "Assignee", render: (a) => a.assignee.full_name },
    { key: "source", header: "Source", render: (a) => SOURCE_LABEL[a.source] },
  ];
  const taskColumns: Column<CommandCenterEmployeeDetail["assigned_tasks"][number]>[] = [
    { key: "title", header: "Task", render: (t) => t.title },
    { key: "department", header: "Department", render: (t) => t.department.code },
    { key: "category", header: "Category", render: (t) => t.category?.name ?? "—" },
    { key: "priority", header: "Priority", render: (t) => PRIORITY_LABEL[t.priority] },
    { key: "assigned", header: "Assigned at", render: (t) => <DateTimeText value={t.assigned_at} /> },
    { key: "deadline", header: "Deadline", render: (t) => <DateTimeText value={t.deadline} /> },
    { key: "status", header: "Status", render: (t) => STATUS_LABEL[t.status] },
    { key: "sla", header: "SLA state", render: (t) => <SlaStateChip state={t.sla_state} /> },
    { key: "completed", header: "Completed at", render: (t) => <DateTimeText value={t.completed_at} /> },
    { key: "source", header: "Source", render: (t) => SOURCE_LABEL[t.source] },
  ];
  return (
    <Paper variant="outlined" sx={{ p: 2, mt: 2 }} aria-label={`Drill-down: ${employee.full_name}`}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center", mb: 1 }}>
        <Typography variant="h4" component="h3">{employee.full_name} — read-only</Typography>
        <Button onClick={onClose}>Close</Button>
      </Stack>
      <SectionError error={detail.error} onRetry={() => void detail.refetch()} what="this employee's work" />
      {detail.isPending && <Typography>Loading employee details…</Typography>}
      {detail.data && (
        <Stack spacing={2}>
          <AttentionChips states={detail.data.attention} />
          <DataTable caption="Employee daily activities" columns={dailyColumns} rows={detail.data.daily_activities} getRowId={(a) => a.task_id}
            loading={detail.isFetching} total={detail.data.daily_activities.length} page={0} onPageChange={() => undefined}
            emptyMessage="No daily activities for the selected filters." />
          <DataTable caption="Employee assigned tasks" columns={taskColumns} rows={detail.data.assigned_tasks} getRowId={(t) => t.task_id}
            loading={detail.isFetching} total={detail.data.assigned_tasks.length} page={0} onPageChange={() => undefined}
            emptyMessage="No assigned tasks for the selected filters." />
        </Stack>
      )}
    </Paper>
  );
}

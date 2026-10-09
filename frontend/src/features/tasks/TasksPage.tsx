import { Alert, Button, Chip, Collapse, MenuItem, Stack, Tab, Tabs, TextField, Typography } from "@mui/material";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { departmentsApi, employeesApi, tasksApi, type TaskListParams } from "../../api/endpoints";
import { PERM, TASK_PRIORITIES, TASK_STATUSES, type Task, type TaskView } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText } from "../../components/DateTimeText";
import { acknowledgmentLabel, TASK_PRIORITY_LABEL, STATUS_LABEL, VERIFICATION_LABEL } from "./labels";
import { SlaBadge } from "./SlaBadge";
import { DailyActivities } from "./DailyActivities";
import { SourceBadge } from "./SourceBadge";
import { TaskFormDialog } from "./TaskFormDialog";

/** My Tasks: Received (assigned to me) and Sent (created by me) are separate views of the same
 * task records; "All" lists everything the backend lets this user see.
 * Phase 5: Received tasks are split into the daily responsibilities the system generated and the
 * tasks a person assigned; every row carries a SCHEDULED / MANUAL badge.
 * Phase 5.1: the daily section shows each activity's start, deadline, SLA state and a countdown. */
export function TasksPage() {
  const { hasPerm } = useAuth();
  const canCreate = hasPerm(PERM.createTask);
  const canSeeMore = hasPerm(PERM.viewAllTasks) || hasPerm(PERM.viewTeamTasks);
  const canSeeEmployees = hasPerm(PERM.viewAllEmployees) || hasPerm(PERM.viewTeamEmployees);

  const [view, setView] = useState<TaskView>("received");
  const [page, setPage] = useState(0);
  const [source, setSource] = useState<"" | "scheduled" | "manual">("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [assignee, setAssignee] = useState("");
  const [department, setDepartment] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [creating, setCreating] = useState(false);
  const [moreFilters, setMoreFilters] = useState(false);
  const navigate = useNavigate();
  const deleted = (useLocation().state as { deleted?: string } | null)?.deleted;
  const openTask = (id: number) => navigate(`/tasks/${id}`);

  const { data: departments = [] } = useQuery({ queryKey: ["departments"], queryFn: departmentsApi.list, enabled: hasPerm(PERM.viewAllTasks) });
  const { data: people } = useQuery({
    queryKey: ["employees", { page: 1, is_active: true }],
    queryFn: () => employeesApi.list({ page: 1, is_active: true }),
    enabled: canSeeEmployees,
  });

  const split = view === "received";
  const filters: TaskListParams = { view, search, status, priority, assignee, department, from, to };
  const params: TaskListParams = { ...filters, page: page + 1, source: split ? "manual" : source };
  const { data, isFetching, error } = useQuery({
    queryKey: ["tasks", params], queryFn: () => tasksApi.list(params), placeholderData: keepPreviousData,
  });
  const reset = (setter: (v: string) => void) => (value: string) => { setter(value); setPage(0); };

  const columns: Column<Task>[] = [
    { key: "title", header: "Task", render: (t) => <Stack><strong>{t.title}</strong><Typography variant="caption" color="text.secondary">{t.reference}</Typography></Stack> },
    { key: "source", header: "Source", render: (t) => <SourceBadge source={t.source} /> },
    { key: "assignee", header: view === "sent" ? "Sent to" : "Assignee", render: (t) => t.assigned_to.full_name },
    { key: "priority", header: "Priority", render: (t) => TASK_PRIORITY_LABEL[t.priority] },
    { key: "status", header: "Status", render: (t) => <Chip size="small" label={STATUS_LABEL[t.status]} color={t.status === "BLOCKED" ? "warning" : "default"} /> },
    { key: "sla", header: "SLA", render: (t) => <SlaBadge clock={t.sla.resolution} note={t.sla.resolution_note} /> },
    { key: "ack", header: "Acknowledgement", render: (t) => acknowledgmentLabel(t) },
    { key: "verification", header: "Verification", render: (t) => VERIFICATION_LABEL[t.verification_status] },
    { key: "received", header: "Received", render: (t) => <DateTimeText value={t.received_at} /> },
    { key: "created", header: "Created", render: (t) => <DateTimeText value={t.created_at} /> },
    { key: "open", header: "", render: (t) => <Button size="small" onClick={() => openTask(t.id)}>Open</Button> },
  ];

  return (
    <Stack spacing={3}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ alignItems: { sm: "center" }, justifyContent: "space-between" }}>
        <Typography variant="h2" component="h1">Tasks</Typography>
        {canCreate && <Button variant="contained" onClick={() => setCreating(true)}>New task</Button>}
      </Stack>
      {deleted && <Alert severity="success">Task {deleted} was deleted.</Alert>}
      <Tabs value={view} onChange={(_, value: TaskView) => { setView(value); setPage(0); }} aria-label="Task views">
        <Tab value="received" label="Received tasks" />
        <Tab value="sent" label="Sent tasks" />
        {canSeeMore && <Tab value="all" label="All permitted tasks" />}
      </Tabs>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
        <TextField label="Search title or description" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
        <TextField select label="Status" value={status} onChange={(e) => reset(setStatus)(e.target.value)} sx={{ minWidth: 150 }}>
          <MenuItem value="">All statuses</MenuItem>
          {TASK_STATUSES.map((s) => <MenuItem key={s} value={s}>{STATUS_LABEL[s]}</MenuItem>)}
        </TextField>
        <TextField select label="Priority" value={priority} onChange={(e) => reset(setPriority)(e.target.value)} sx={{ minWidth: 140 }}>
          <MenuItem value="">All priorities</MenuItem>
          {TASK_PRIORITIES.map((p) => <MenuItem key={p} value={p}>{TASK_PRIORITY_LABEL[p]}</MenuItem>)}
        </TextField>
        <Button variant="text" onClick={() => setMoreFilters((v) => !v)} aria-expanded={moreFilters}>
          {moreFilters ? "Fewer filters" : "More filters"}
        </Button>
      </Stack>
      <Collapse in={moreFilters} unmountOnExit>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
        {canSeeEmployees && (
          <TextField select label="Assignee" value={assignee} onChange={(e) => reset(setAssignee)(e.target.value)} sx={{ minWidth: 170 }}>
            <MenuItem value="">Anyone</MenuItem>
            {(people?.results ?? []).map((p) => <MenuItem key={p.id} value={p.id}>{p.full_name}</MenuItem>)}
          </TextField>
        )}
        {hasPerm(PERM.viewAllTasks) && (
          <TextField select label="Department" value={department} onChange={(e) => reset(setDepartment)(e.target.value)} sx={{ minWidth: 150 }}>
            <MenuItem value="">All departments</MenuItem>
            {departments.map((d) => <MenuItem key={d.id} value={d.id}>{d.code}</MenuItem>)}
          </TextField>
        )}
        {!split && (
          <TextField select label="Source" value={source} onChange={(e) => { setSource(e.target.value as typeof source); setPage(0); }} sx={{ minWidth: 150 }}>
            <MenuItem value="">Scheduled and manual</MenuItem>
            <MenuItem value="scheduled">Scheduled</MenuItem>
            <MenuItem value="manual">Manual</MenuItem>
          </TextField>
        )}
        <TextField label="Created from" type="date" value={from} onChange={(e) => reset(setFrom)(e.target.value)} InputLabelProps={{ shrink: true }} />
        <TextField label="Created to" type="date" value={to} onChange={(e) => reset(setTo)(e.target.value)} InputLabelProps={{ shrink: true }} />
      </Stack>
      </Collapse>
      <ApiErrorAlert error={error} />
      {split ? (
        <>
          <Typography variant="h3" component="h2">Daily / scheduled responsibilities</Typography>
          <DailyActivities />
          <Typography variant="h3" component="h2">Assigned tasks</Typography>
          <DataTable
            caption="Assigned tasks"
            columns={columns} rows={data?.results ?? []} getRowId={(t) => t.id} loading={isFetching}
            total={data?.count ?? 0} page={page} onPageChange={setPage}
            emptyMessage="No tasks are assigned to you."
          />
        </>
      ) : (
        <DataTable
          caption={view === "sent" ? "Sent tasks" : "All permitted tasks"}
          columns={columns} rows={data?.results ?? []} getRowId={(t) => t.id} loading={isFetching}
          total={data?.count ?? 0} page={page} onPageChange={setPage}
          emptyMessage={view === "sent" ? "You have not sent any tasks." : "No tasks match these filters."}
        />
      )}
      <TaskFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(task) => { setCreating(false); navigate(`/tasks/${task.id}`, { state: { created: task.reference } }); }}
      />
    </Stack>
  );
}

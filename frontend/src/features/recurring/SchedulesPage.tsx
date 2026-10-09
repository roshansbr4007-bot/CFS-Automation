import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Stack, Switch, Tab, Tabs,
  TextField, Typography,
} from "@mui/material";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";

import { ApiError } from "../../api/apiClient";
import { schedulesApi } from "../../api/endpoints";
import type { NonWorkingDayPolicy, OccurrenceStatus, RecurringSchedule, ScheduleOccurrence } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText, formatBusinessDate } from "../../components/DateTimeText";
import { todayIST, WEEKDAY_SHORT, WeekdayPicker } from "./ScheduleFields";

const POLICY_LABEL: Record<NonWorkingDayPolicy, string> = {
  SKIP: "Skip non-working days", NEXT_WORKING_DAY: "Next working day", PREVIOUS_WORKING_DAY: "Previous working day",
};
const STATUS_COLOR: Record<OccurrenceStatus, "success" | "warning" | "error" | "default"> = {
  GENERATED: "success", SKIPPED: "warning", MISSED: "default", FAILED: "error",
};

/** Recurring schedules of responsibilities and the ledger of what the scheduler did. */
export function SchedulesPage() {
  const [tab, setTab] = useState<"schedules" | "occurrences">("schedules");
  return (
    <Stack spacing={3}>
      <Typography variant="h2" component="h1">Recurring schedules</Typography>
      <Tabs value={tab} onChange={(_, v: "schedules" | "occurrences") => setTab(v)} aria-label="Schedule views">
        <Tab value="schedules" label="Schedules" />
        <Tab value="occurrences" label="Occurrences" />
      </Tabs>
      {tab === "schedules" ? <ScheduleList /> : <OccurrenceList />}
    </Stack>
  );
}

/** Phase A: whoever manages a schedule's responsibility may edit it (server-decided `can_manage`). */
function ScheduleList() {
  const { data = [], isFetching, error } = useQuery({ queryKey: ["recurring-schedules"], queryFn: schedulesApi.list });
  const [editing, setEditing] = useState<RecurringSchedule | null>(null);
  const columns: Column<RecurringSchedule>[] = [
    { key: "title", header: "Schedule", render: (s) => <Stack><strong>{s.title}</strong><Typography variant="caption" color="text.secondary">{s.responsibility.name}</Typography></Stack> },
    { key: "frequency", header: "Repeats", render: describeRepeats },
    { key: "time", header: "Time (IST)", render: (s) => s.run_time.slice(0, 5) },
    { key: "policy", header: "Non-working day", render: (s) => POLICY_LABEL[s.non_working_day_policy] },
    { key: "from", header: "Effective", render: (s) => `${formatBusinessDate(s.effective_from)}${s.effective_to ? ` – ${formatBusinessDate(s.effective_to)}` : ""}` },
    { key: "active", header: "Status", render: (s) => <Chip size="small" label={s.is_active ? "Active" : "Inactive"} color={s.is_active ? "success" : "default"} /> },
    { key: "edit", header: "", render: (s) => s.can_manage ? <Button size="small" onClick={() => setEditing(s)}>Edit</Button> : null },
  ];
  return (
    <>
      <ApiErrorAlert error={error} />
      <DataTable caption="Recurring schedules" columns={columns} rows={data} getRowId={(s) => s.id} loading={isFetching}
        total={data.length} page={0} onPageChange={() => undefined} emptyMessage="No schedules." />
      <ScheduleDialog schedule={editing} onClose={() => setEditing(null)} />
    </>
  );
}

/** How a schedule repeats, in words (Phase B adds weekly and specific-date schedules). */
export function describeRepeats(s: RecurringSchedule): string {
  if (s.frequency === "WEEKLY") return `Weekly — ${(s.weekdays ?? []).map((d) => WEEKDAY_SHORT[d]).join(", ")}`;
  if (s.frequency === "ONCE") return s.run_date ? `Once — ${formatBusinessDate(s.run_date)}` : "Once";
  return s.frequency === "DAILY" ? "Every working day" : `Monthly on day ${s.day_of_month}`;
}

function ScheduleDialog({ schedule, onClose }: { schedule: RecurringSchedule | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState(""); const [runTime, setRunTime] = useState(""); const [day, setDay] = useState("");
  const [policy, setPolicy] = useState<NonWorkingDayPolicy>("SKIP"); const [active, setActive] = useState(true);
  const [weekdays, setWeekdays] = useState<number[]>([]); const [runDate, setRunDate] = useState(""); // Phase B
  useEffect(() => {
    if (!schedule) return;
    setTitle(schedule.title); setRunTime(schedule.run_time.slice(0, 5)); setDay(schedule.day_of_month ? String(schedule.day_of_month) : "");
    setPolicy(schedule.non_working_day_policy); setActive(schedule.is_active);
    setWeekdays(schedule.weekdays ?? []); setRunDate(schedule.run_date ?? "");
  }, [schedule]);
  const mutation = useMutation({
    mutationFn: () => schedulesApi.update((schedule as RecurringSchedule).id, {
      version: (schedule as RecurringSchedule).version, title, run_time: runTime, non_working_day_policy: policy, is_active: active,
      ...(schedule?.frequency === "MONTHLY" ? { day_of_month: Number(day) } : {}),
      ...(schedule?.frequency === "WEEKLY" ? { weekdays } : {}),
      ...(schedule?.frequency === "ONCE" ? { run_date: runDate } : {}),
    }),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["recurring-schedules"] }); onClose(); },
  });
  const apiError = mutation.error instanceof ApiError ? mutation.error : null;
  const fieldError = (name: string) => apiError?.fields?.[name]?.join(" ");
  const weekdaysMissing = schedule?.frequency === "WEEKLY" && weekdays.length === 0;
  const dateMissing = schedule?.frequency === "ONCE" && !runDate;
  return (
    <Dialog open={schedule !== null} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Edit schedule</DialogTitle>
      <DialogContent><Stack spacing={2} sx={{ mt: 1 }}>
        {apiError && <Alert severity="error">{apiError.message}</Alert>}
        <Typography variant="body2" color="text.secondary">Changes apply to future occurrences only.</Typography>
        <TextField size="small" label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <TextField size="small" label="Time (IST)" type="time" value={runTime} onChange={(e) => setRunTime(e.target.value)} InputLabelProps={{ shrink: true }} />
        {schedule?.frequency === "MONTHLY" && (
          <TextField size="small" label="Day of month (1–28)" type="number" value={day} onChange={(e) => setDay(e.target.value)} />
        )}
        {schedule?.frequency === "WEEKLY" && (
          <WeekdayPicker value={weekdays} onChange={setWeekdays}
            error={weekdaysMissing ? "Choose at least one weekday." : fieldError("weekdays")} />
        )}
        {schedule?.frequency === "ONCE" && (
          <TextField size="small" label="Date" type="date" value={runDate} onChange={(e) => setRunDate(e.target.value)}
            InputLabelProps={{ shrink: true }} inputProps={{ min: todayIST() }}
            error={!!fieldError("run_date")} helperText={fieldError("run_date") ?? "Can change only until it has been generated."} />
        )}
        <TextField size="small" select label="On a non-working day" value={policy} onChange={(e) => setPolicy(e.target.value as NonWorkingDayPolicy)}>
          {(Object.keys(POLICY_LABEL) as NonWorkingDayPolicy[]).map((p) => <MenuItem key={p} value={p}>{POLICY_LABEL[p]}</MenuItem>)}
        </TextField>
        <FormControlLabel control={<Switch checked={active} onChange={(e) => setActive(e.target.checked)} />} label="Active" />
      </Stack></DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!title.trim() || !runTime || weekdaysMissing || dateMissing || mutation.isPending}
          onClick={() => mutation.mutate()}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

function OccurrenceList() {
  const [page, setPage] = useState(0);
  const [status, setStatus] = useState("");
  const params = { page: page + 1, status };
  const { data, isFetching, error } = useQuery({
    queryKey: ["schedule-occurrences", params], queryFn: () => schedulesApi.occurrences(params), placeholderData: keepPreviousData,
  });
  const columns: Column<ScheduleOccurrence>[] = [
    { key: "date", header: "Business date", render: (o) => formatBusinessDate(o.occurrence_date) },
    { key: "responsibility", header: "Responsibility", render: (o) => o.responsibility.name },
    { key: "status", header: "Result", render: (o) => <Chip size="small" label={o.status} color={STATUS_COLOR[o.status]} /> },
    { key: "assignee", header: "Assigned to", render: (o) => o.assignee?.full_name ?? "—" },
    { key: "task", header: "Task", render: (o) => o.task ? <Button size="small" component={RouterLink} to={`/tasks/${o.task.id}`}>{o.task.reference}</Button> : "—" },
    { key: "generated", header: "Generated", render: (o) => <DateTimeText value={o.generated_at} /> },
    { key: "detail", header: "Detail", render: (o) => o.detail || "—" },
  ];
  return (
    <>
      <TextField size="small" select label="Result" value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }} sx={{ maxWidth: 220 }}>
        <MenuItem value="">All results</MenuItem>
        {(["GENERATED", "SKIPPED", "MISSED", "FAILED"] as OccurrenceStatus[]).map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
      </TextField>
      <ApiErrorAlert error={error} />
      <DataTable caption="Occurrences" columns={columns} rows={data?.results ?? []} getRowId={(o) => o.id} loading={isFetching}
        total={data?.count ?? 0} page={page} onPageChange={setPage} emptyMessage="The scheduler has not recorded anything yet." />
    </>
  );
}

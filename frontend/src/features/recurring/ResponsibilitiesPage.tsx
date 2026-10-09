import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, MenuItem, Stack,
  Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "../../api/apiClient";
import { departmentsApi, employeesApi, responsibilitiesApi, tasksApi } from "../../api/endpoints";
import { PERM, TASK_PRIORITIES, type Responsibility, type TaskPriority } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { formatBusinessDate } from "../../components/DateTimeText";
import { TASK_PRIORITY_LABEL } from "../tasks/labels";
import {
  AddScheduleDialog, emptySchedule, ScheduleFields, serverErrors, todayIST, toScheduleInput, validateSchedule,
  type ScheduleErrors, type ScheduleFormValue,
} from "./ScheduleFields";

/** "2 h", "1 h 30 min", "45 min" — a responsibility deadline (SLA) in words. */
function formatDeadline(minutes: number | null | undefined): string {
  if (!minutes) return "—";
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return [h ? `${h} h` : "", m ? `${m} min` : ""].filter(Boolean).join(" ");
}

/** Hours / minutes inputs -> total minutes; "" = no deadline; undefined = invalid. */
function deadlineFromInputs(hours: string, minutes: string): number | null | undefined {
  if (!hours.trim() && !minutes.trim()) return null;
  const h = hours.trim() ? Number(hours) : 0, m = minutes.trim() ? Number(minutes) : 0;
  if (!Number.isInteger(h) || !Number.isInteger(m) || h < 0 || m < 0 || m > 59) return undefined;
  return h * 60 + m > 0 ? h * 60 + m : undefined;
}

/** Responsibilities: recurring Operations duties and who owns them over time (Phase 5). The
 * owner on a business date receives that date's generated task; history is never rewritten. */
export function ResponsibilitiesPage() {
  const { hasPerm } = useAuth();
  const canManage = hasPerm(PERM.manageAllResponsibilities) || hasPerm(PERM.manageTeamResponsibilities);
  const { data = [], isFetching, error } = useQuery({ queryKey: ["responsibilities"], queryFn: responsibilitiesApi.list });
  const [editing, setEditing] = useState<Responsibility | "new" | null>(null);
  const [owning, setOwning] = useState<Responsibility | null>(null);
  const [history, setHistory] = useState<Responsibility | null>(null);
  const [scheduling, setScheduling] = useState<Responsibility | null>(null);
  const [archiving, setArchiving] = useState<Responsibility | null>(null);

  const columns: Column<Responsibility>[] = [
    { key: "name", header: "Responsibility", render: (r) => <Stack><strong>{r.name}</strong><Typography variant="caption" color="text.secondary">{r.code}</Typography></Stack> },
    { key: "department", header: "Department", render: (r) => r.department.code },
    { key: "category", header: "Category", render: (r) => r.category.name },
    { key: "owner", header: "Current owner", render: (r) => r.current_owner ? r.current_owner.employee.full_name : <Chip size="small" color="warning" label="No owner" /> },
    { key: "since", header: "Effective from", render: (r) => r.current_owner ? formatBusinessDate(r.current_owner.effective_from) : "—" },
    { key: "schedule", header: "Schedule", render: (r) => r.active_schedule_count > 0
      ? `${r.active_schedule_count} active`
      : <Chip size="small" color="warning" label="No schedule" /> },
    { key: "deadline", header: "Deadline (SLA)", render: (r) => formatDeadline(r.deadline_minutes) },
    { key: "active", header: "Status", render: (r) => <Chip size="small" label={r.is_active ? "Active" : "Archived"} color={r.is_active ? "success" : "default"} /> },
    { key: "actions", header: "", render: (r) => (
      <Stack direction="row" spacing={1}>
        {/* Change Set 1 (D6): an archived responsibility no longer changes (no reactivation here). */}
        {r.is_active && r.can_manage && <Button size="small" onClick={() => setEditing(r)}>Edit</Button>}
        {r.is_active && r.can_manage && <Button size="small" onClick={() => setOwning(r)}>Change owner</Button>}
        {r.is_active && r.can_manage && <Button size="small" onClick={() => setScheduling(r)}>Add schedule</Button>}
        {r.is_active && r.can_manage && <Button size="small" color="warning" onClick={() => setArchiving(r)}>Deactivate</Button>}
        <Button size="small" onClick={() => setHistory(r)}>History</Button>
      </Stack>
    ) },
  ];

  return (
    <Stack spacing={3}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Typography variant="h2" component="h1">Responsibilities</Typography>
        {canManage && <Button variant="contained" onClick={() => setEditing("new")}>Add responsibility</Button>}
      </Stack>
      <Typography color="text.secondary">
        Regular duties generate their tasks automatically. The owner on each business date receives that day's task.
      </Typography>
      <ApiErrorAlert error={error} />
      <DataTable caption="Responsibilities" columns={columns} rows={data} getRowId={(r) => r.id} loading={isFetching}
        total={data.length} page={0} onPageChange={() => undefined} emptyMessage="No responsibilities yet." />
      <ResponsibilityDialog value={editing} onClose={() => setEditing(null)} />
      <OwnerDialog responsibility={owning} onClose={() => setOwning(null)} />
      <DeactivateDialog responsibility={archiving} onClose={() => setArchiving(null)} />
      <HistoryDialog responsibility={history} onClose={() => setHistory(null)} />
      <AddScheduleDialog responsibility={scheduling} onClose={() => setScheduling(null)} />
    </Stack>
  );
}

/** Add = Phase A setup (responsibility + optional owner + first schedule, saved together).
 * Edit = the existing responsibility fields (schedules and owners have their own actions). */
function ResponsibilityDialog({ value, onClose }: { value: Responsibility | "new" | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { hasPerm } = useAuth();
  const allowPast = hasPerm(PERM.manageSchedules);
  const open = value !== null;
  const existing = value && value !== "new" ? value : null;
  const { data: departments = [] } = useQuery({ queryKey: ["departments"], queryFn: departmentsApi.list, enabled: open });
  const { data: categories = [] } = useQuery({ queryKey: ["task-categories"], queryFn: tasksApi.categories, enabled: open });
  const { data: templates = [] } = useQuery({ queryKey: ["task-templates"], queryFn: tasksApi.templates, enabled: open });
  const { data: people } = useQuery({
    queryKey: ["employees", { page: 1, is_active: true }], queryFn: () => employeesApi.list({ page: 1, is_active: true }),
    enabled: open && !existing,
  });
  const [code, setCode] = useState(""); const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const [department, setDepartment] = useState(""); const [category, setCategory] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("MEDIUM");
  const [template, setTemplate] = useState("");
  const [owner, setOwner] = useState(""); const [ownerFrom, setOwnerFrom] = useState(todayIST());
  const [schedule, setSchedule] = useState<ScheduleFormValue>(emptySchedule());
  const [checked, setChecked] = useState(false);
  // Responsibility deadline (SLA): HR / Admin only; read-only for an Operations Manager.
  const canDeadline = existing ? !!existing.can_manage_deadline : hasPerm(PERM.manageAllResponsibilities);
  const initialDeadline = existing?.deadline_minutes ?? null;
  const [deadlineHours, setDeadlineHours] = useState(""); const [deadlineMinutes, setDeadlineMinutes] = useState("");
  useEffect(() => {
    if (!open) return;
    setCode(existing?.code ?? ""); setName(existing?.name ?? ""); setDescription(existing?.description ?? "");
    setDepartment(existing ? String(existing.department.id) : ""); setCategory(existing ? String(existing.category.id) : "");
    setPriority(existing?.priority ?? "MEDIUM");
    setTemplate(existing?.template ? String(existing.template.id) : "");
    setOwner(""); setOwnerFrom(todayIST()); setSchedule(emptySchedule()); setChecked(false);
    const d = existing?.deadline_minutes ?? null;
    setDeadlineHours(d ? String(Math.floor(d / 60)) : ""); setDeadlineMinutes(d ? String(d % 60) : "");
  }, [open, existing]);
  const deadline = deadlineFromInputs(deadlineHours, deadlineMinutes);
  // Sent only by HR / Admin and only when it changes (never for an Operations Manager).
  const deadlinePayload = canDeadline && deadline !== undefined && deadline !== initialDeadline ? { deadline_minutes: deadline } : {};
  const mutation = useMutation({
    mutationFn: () => existing
      ? responsibilitiesApi.update(existing.id, {
        version: existing.version, name, description, category: Number(category), priority,
        template: template ? Number(template) : null, ...deadlinePayload,
      })
      : responsibilitiesApi.setup({
        code, name, description, department: Number(department), category: Number(category), priority,
        ...(template ? { template: Number(template) } : {}),
        owner: owner ? { employee: Number(owner), effective_from: ownerFrom } : null,
        schedule: toScheduleInput(schedule), ...deadlinePayload,
      }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["responsibilities"] }),
        qc.invalidateQueries({ queryKey: ["recurring-schedules"] }),
      ]);
      onClose();
    },
  });
  const apiError = mutation.error instanceof ApiError ? mutation.error : null;
  const fieldError = serverErrors(mutation.error);
  const scheduleErrors: ScheduleErrors = { ...serverErrors(mutation.error, "schedule"), ...(checked ? validateSchedule(schedule, allowPast) : {}) };
  const ownerErrors = serverErrors(mutation.error, "owner");
  const ownerFromProblem = checked && owner && (!ownerFrom || ownerFrom < todayIST()) ? "Ownership cannot start in the past." : undefined;
  const basicsMissing = !name.trim() || !category || (!existing && (!code.trim() || !department));
  const departmentTemplates = templates.filter((t) => !department || String(t.department.id) === department);

  const deadlineProblem = canDeadline && deadline === undefined ? "Enter whole hours and 0–59 minutes, more than 0 in total." : undefined;
  const save = () => {
    setChecked(true);
    if (basicsMissing || deadlineProblem) return;
    if (!existing && (Object.keys(validateSchedule(schedule, allowPast)).length > 0 || (owner && (!ownerFrom || ownerFrom < todayIST())))) return;
    mutation.mutate();
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="responsibility-title">
      <DialogTitle id="responsibility-title">{existing ? "Edit responsibility" : "Add responsibility"}</DialogTitle>
      <DialogContent><Stack spacing={2} sx={{ mt: 1 }}>
        {apiError && <Alert severity="error">{apiError.message}</Alert>}
        <TextField size="small" label="Code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} disabled={!!existing}
          error={!!fieldError.code} helperText={fieldError.code} />
        <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} error={!!fieldError.name} helperText={fieldError.name} />
        <TextField size="small" label="Description / instructions" value={description} onChange={(e) => setDescription(e.target.value)} multiline minRows={1} />
        <TextField size="small" select label="Department" value={department} onChange={(e) => { setDepartment(String(e.target.value)); setTemplate(""); }}
          disabled={!!existing} error={!!fieldError.department} helperText={fieldError.department}>
          {departments.map((d) => <MenuItem key={d.id} value={String(d.id)}>{d.code} — {d.name}</MenuItem>)}
        </TextField>
        <TextField size="small" select label="Category" value={category} onChange={(e) => setCategory(String(e.target.value))}
          error={!!fieldError.category} helperText={fieldError.category}>
          {categories.map((c) => <MenuItem key={c.id} value={String(c.id)}>{c.name}</MenuItem>)}
        </TextField>
        <TextField size="small" select label="Priority" value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
          {TASK_PRIORITIES.map((p) => <MenuItem key={p} value={p}>{TASK_PRIORITY_LABEL[p]}</MenuItem>)}
        </TextField>
        <TextField size="small" select label="Task type" value={template} onChange={(e) => setTemplate(String(e.target.value))}
          error={!!fieldError.template} helperText={fieldError.template ?? "Sets the SLA (for example, within hours of login)."}>
          <MenuItem value="">None (priority SLA)</MenuItem>
          {departmentTemplates.map((t) => <MenuItem key={t.id} value={String(t.id)}>{t.name}</MenuItem>)}
        </TextField>
        <Typography variant="subtitle2" component="h3">Responsibility deadline (SLA)</Typography>
        <Stack component="fieldset" aria-label="Responsibility deadline (SLA)" spacing={1} sx={{ border: 0, p: 0, m: 0 }}>
          <Stack direction="row" spacing={2}>
            <TextField size="small" type="number" label="Deadline hours" value={deadlineHours} disabled={!canDeadline}
              onChange={(e) => setDeadlineHours(e.target.value)} inputProps={{ min: 0 }} sx={{ flex: 1 }} error={!!deadlineProblem} />
            <TextField size="small" type="number" label="Deadline minutes" value={deadlineMinutes} disabled={!canDeadline}
              onChange={(e) => setDeadlineMinutes(e.target.value)} inputProps={{ min: 0, max: 59 }} sx={{ flex: 1 }} error={!!deadlineProblem} />
          </Stack>
          <Typography variant="caption" color={deadlineProblem || fieldError.deadline_minutes ? "error" : "text.secondary"}>
            {deadlineProblem ?? fieldError.deadline_minutes ?? (canDeadline
              ? "Leave empty for no responsibility deadline. When set, generated tasks use it instead of the task type or priority SLA."
              : "Only HR or Admin can configure the responsibility deadline.")}
          </Typography>
        </Stack>
        {!existing && (
          <>
            <Typography variant="subtitle2" component="h3">Owner (optional)</Typography>
            <Stack direction="row" spacing={2}>
              <TextField size="small" select label="Owner" value={owner} onChange={(e) => setOwner(String(e.target.value))} sx={{ flex: 1 }}
                error={!!ownerErrors.employee} helperText={ownerErrors.employee}>
                <MenuItem value="">No owner yet</MenuItem>
                {(people?.results ?? []).map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name} ({p.department.code})</MenuItem>)}
              </TextField>
              <TextField size="small" label="Owner from" type="date" value={ownerFrom} onChange={(e) => setOwnerFrom(e.target.value)}
                InputLabelProps={{ shrink: true }} inputProps={{ min: todayIST() }} disabled={!owner}
                error={!!(ownerFromProblem ?? ownerErrors.effective_from)} helperText={ownerFromProblem ?? ownerErrors.effective_from} />
            </Stack>
            <Typography variant="subtitle2" component="h3">Schedule</Typography>
            <ScheduleFields value={schedule} onChange={setSchedule} errors={scheduleErrors} allowPast={allowPast} />
          </>
        )}
      </Stack></DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={basicsMissing || mutation.isPending} onClick={save}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

/** Change Set 1 (D6): deactivate (archive) through the existing update with its audit. Nothing is
 * deleted; it simply stops generating work. There is no reactivation in the UI. */
function DeactivateDialog({ responsibility, onClose }: { responsibility: Responsibility | null; onClose: () => void }) {
  const qc = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => {
      const r = responsibility as Responsibility;
      return responsibilitiesApi.update(r.id, { version: r.version, is_active: false });
    },
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["responsibilities"] }),
        qc.invalidateQueries({ queryKey: ["recurring-schedules"] }),
      ]);
      onClose();
    },
  });
  useEffect(() => { if (responsibility) mutation.reset(); }, [responsibility]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog open={responsibility !== null} onClose={onClose} fullWidth maxWidth="xs" aria-labelledby="deactivate-title">
      <DialogTitle id="deactivate-title">Deactivate {responsibility?.name}?</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          <ApiErrorAlert error={mutation.error} />
          <DialogContentText>
            No new tasks will be generated. Its owners, schedules, generated tasks and history are kept.
          </DialogContentText>
          <DialogContentText>
            An archived responsibility can no longer be edited, given a new owner or a new schedule.
          </DialogContentText>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="warning" disabled={mutation.isPending} onClick={() => mutation.mutate()}>Deactivate</Button>
      </DialogActions>
    </Dialog>
  );
}

function OwnerDialog({ responsibility, onClose }: { responsibility: Responsibility | null; onClose: () => void }) {
  const qc = useQueryClient();
  const open = responsibility !== null;
  const { data: people } = useQuery({
    queryKey: ["employees", { page: 1, is_active: true }], queryFn: () => employeesApi.list({ page: 1, is_active: true }), enabled: open,
  });
  const [employee, setEmployee] = useState(""); const [from, setFrom] = useState(""); const [note, setNote] = useState("");
  const [lastDay, setLastDay] = useState("");
  useEffect(() => { if (open) { setEmployee(""); setFrom(""); setNote(""); setLastDay(""); } }, [open]);
  const done = async () => { await qc.invalidateQueries({ queryKey: ["responsibilities"] }); onClose(); };
  const assign = useMutation({
    mutationFn: () => responsibilitiesApi.assignOwner((responsibility as Responsibility).id, { employee: Number(employee), effective_from: from, note }),
    onSuccess: done,
  });
  const end = useMutation({
    mutationFn: () => responsibilitiesApi.endOwnership((responsibility as Responsibility).id, { last_day: lastDay, note }),
    onSuccess: done,
  });
  const apiError = [assign.error, end.error].find((e) => e instanceof ApiError) as ApiError | undefined;
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="owner-title">
      <DialogTitle id="owner-title">Owner of {responsibility?.name}</DialogTitle>
      <DialogContent><Stack spacing={2} sx={{ mt: 1 }}>
        {apiError && <Alert severity="error">{apiError.message}</Alert>}
        <Typography variant="body2">
          Current owner: {responsibility?.current_owner ? responsibility.current_owner.employee.full_name : "nobody"}.
          Tasks already generated keep their assignee.
        </Typography>
        <TextField size="small" select label="New owner" value={employee} onChange={(e) => setEmployee(String(e.target.value))}>
          {(people?.results ?? []).map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name} ({p.department.code})</MenuItem>)}
        </TextField>
        <TextField size="small" label="Owner from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} InputLabelProps={{ shrink: true }} />
        <TextField size="small" label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
        {responsibility?.current_owner && (
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <TextField size="small" label="Or end current ownership after" type="date" value={lastDay} onChange={(e) => setLastDay(e.target.value)} InputLabelProps={{ shrink: true }} />
            <Button disabled={!lastDay || end.isPending} onClick={() => end.mutate()}>End ownership</Button>
          </Stack>
        )}
      </Stack></DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!employee || !from || assign.isPending} onClick={() => assign.mutate()}>Assign owner</Button>
      </DialogActions>
    </Dialog>
  );
}

function HistoryDialog({ responsibility, onClose }: { responsibility: Responsibility | null; onClose: () => void }) {
  const open = responsibility !== null;
  const { data = [] } = useQuery({
    queryKey: ["responsibility-owners", responsibility?.id], queryFn: () => responsibilitiesApi.owners((responsibility as Responsibility).id), enabled: open,
  });
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="history-title">
      <DialogTitle id="history-title">Ownership history — {responsibility?.name}</DialogTitle>
      <DialogContent>
        {data.length === 0 ? <Typography>No owner has been assigned yet.</Typography> : (
          <Table size="small" aria-label="Ownership history">
            <TableHead><TableRow><TableCell>Owner</TableCell><TableCell>From</TableCell><TableCell>To</TableCell><TableCell>Note</TableCell></TableRow></TableHead>
            <TableBody>
              {data.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>{row.employee.full_name}</TableCell>
                  <TableCell>{formatBusinessDate(row.effective_from)}</TableCell>
                  <TableCell>{row.superseded_at ? "corrected (replaced the same day)" : row.effective_to ? formatBusinessDate(row.effective_to) : "current"}</TableCell>
                  <TableCell>{row.note || "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
}

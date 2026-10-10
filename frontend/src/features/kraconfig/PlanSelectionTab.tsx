/** Phase 7.5A: which plan applies to whom. Department + system role defaults, HR overrides for
 * one employee (reason required), and a read-only "which plan applies?" check. The selection
 * logic itself is the backend's and is unchanged: the check shows its answer as given. */
import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Paper, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "../../api/apiClient";
import { departmentsApi, employeesApi } from "../../api/endpoints";
import { PERM, type KpiCfgOverride, type KpiCfgPlanDefault, type KpiCfgResolution } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { formatBusinessDate } from "../../components/DateTimeText";
import { todayIST } from "../recurring/ScheduleFields";
import { cfgApi, cfgErrors, cfgKeys, useCfgMutation, usePlans } from "./api";
import { ProblemList } from "./LifecycleDialogs";
import {
  MODEL_LABEL, period, planTitle, RESOLUTION_SOURCE_LABEL, RESOLUTION_STATE_LABEL, SYSTEM_ROLES,
} from "./labels";

export function PlanSelectionTab() {
  return (
    <Stack spacing={4}>
      <DefaultsSection />
      <OverridesSection />
      <ResolutionCheck />
    </Stack>
  );
}

// --- department + system role defaults ------------------------------------------------------

function DefaultsSection() {
  const { hasPerm } = useAuth();
  const canPrepare = hasPerm(PERM.configureKpis);
  const { data = [], error } = useQuery({ queryKey: cfgKeys.defaults, queryFn: cfgApi.defaults });
  const [adding, setAdding] = useState(false);
  const [ending, setEnding] = useState<KpiCfgPlanDefault | null>(null);
  const [endOpen, setEndOpen] = useState(false); // the row stays set while its dialog closes
  return (
    <Stack component="section" aria-label="Plan defaults" spacing={1.5}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Typography variant="h6" component="h2">Department and role defaults</Typography>
        {canPrepare && <Button variant="outlined" onClick={() => setAdding(true)}>Add default</Button>}
      </Stack>
      <Typography variant="body2" color="text.secondary">
        Without an override, an employee gets the active plan of the family set for their department and system role.
      </Typography>
      <ApiErrorAlert error={error} />
      <Paper>
        <Table size="small" aria-label="Plan defaults table">
          <TableHead>
            <TableRow>
              <TableCell>Department</TableCell><TableCell>System role</TableCell><TableCell>Plan family</TableCell>
              <TableCell>Effective</TableCell>{canPrepare && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {data.length === 0 && <TableRow><TableCell colSpan={canPrepare ? 5 : 4}>No defaults yet.</TableCell></TableRow>}
            {data.map((d) => (
              <TableRow key={d.id}>
                <TableCell>{d.department.code} — {d.department.name}</TableCell>
                <TableCell>{d.role}</TableCell>
                <TableCell>{d.configuration}</TableCell>
                <TableCell>{period(d.effective_from, d.effective_to)}</TableCell>
                {canPrepare && (
                  <TableCell>
                    <Button size="small" onClick={() => { setEnding(d); setEndOpen(true); }} aria-label={`End default ${d.department.code} ${d.role}`}>End</Button>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>
      <AddDefaultDialog open={adding} onClose={() => setAdding(false)} />
      <EndDialog
        open={endOpen}
        title={ending ? `End the default ${ending.department.code} / ${ending.role}` : ""}
        withReason={false}
        run={(body) => cfgApi.endDefault((ending as KpiCfgPlanDefault).id, body)}
        onClose={() => setEndOpen(false)}
      />
    </Stack>
  );
}

function AddDefaultDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data: departments = [] } = useQuery({ queryKey: ["departments"], queryFn: departmentsApi.list, enabled: open });
  const { data: plans = [] } = usePlans(open);
  const families = [...new Set(plans.filter((p) => p.calculation_model === "KRA_POINTS").map((p) => p.configuration))].sort();
  const [department, setDepartment] = useState("");
  const [role, setRole] = useState("");
  const [family, setFamily] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const save = useCfgMutation(() => cfgApi.createDefault({
    department: Number(department), role, configuration: family, effective_from: from, effective_to: to || null,
  }), () => onClose());
  useEffect(() => {
    if (open) { setDepartment(""); setRole(""); setFamily(""); setFrom(""); setTo(""); save.reset(); }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(save.error);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="default-title">
      <DialogTitle id="default-title">Add plan default</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {save.error instanceof ApiError && Object.keys(errors).length === 0 && <Alert severity="error">{save.error.message}</Alert>}
          <TextField size="small" select label="Department" value={department} onChange={(e) => setDepartment(String(e.target.value))}
            error={!!errors.department} helperText={errors.department}>
            {departments.map((d) => <MenuItem key={d.id} value={String(d.id)}>{d.code} — {d.name}</MenuItem>)}
          </TextField>
          <TextField size="small" select label="System role" value={role} onChange={(e) => setRole(String(e.target.value))}
            error={!!errors.role} helperText={errors.role}>
            {SYSTEM_ROLES.map((r) => <MenuItem key={r} value={r}>{r}</MenuItem>)}
          </TextField>
          <TextField size="small" select label="Plan family" value={family} onChange={(e) => setFamily(String(e.target.value))}
            error={!!errors.configuration} helperText={errors.configuration ?? "The active version of this family on each date applies."}>
            {families.map((f) => <MenuItem key={f} value={f}>{f}</MenuItem>)}
          </TextField>
          <Stack direction="row" spacing={2}>
            <TextField size="small" type="date" label="Effective from" value={from} onChange={(e) => setFrom(e.target.value)} sx={{ flex: 1 }}
              InputLabelProps={{ shrink: true }} error={!!errors.effective_from} helperText={errors.effective_from} />
            <TextField size="small" type="date" label="Effective to (optional)" value={to} onChange={(e) => setTo(e.target.value)} sx={{ flex: 1 }}
              InputLabelProps={{ shrink: true }} error={!!errors.effective_to} helperText={errors.effective_to} />
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!department || !role || !family || !from || save.isPending} onClick={() => save.mutate()}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

// --- HR overrides ------------------------------------------------------------------------------

function OverridesSection() {
  const { hasPerm } = useAuth();
  const canPrepare = hasPerm(PERM.configureKpis);
  const { data = [], error } = useQuery({ queryKey: cfgKeys.overrides, queryFn: cfgApi.overrides });
  const [adding, setAdding] = useState(false);
  const [ending, setEnding] = useState<KpiCfgOverride | null>(null);
  const [endOpen, setEndOpen] = useState(false); // the row stays set while its dialog closes
  return (
    <Stack component="section" aria-label="Employee overrides" spacing={1.5}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Typography variant="h6" component="h2">Employee overrides</Typography>
        {canPrepare && <Button variant="outlined" onClick={() => setAdding(true)}>Add override</Button>}
      </Stack>
      <Typography variant="body2" color="text.secondary">
        An override gives one employee a specific active plan version for a period, whatever the default says.
        Legacy (0–100) assignments are listed here too.
      </Typography>
      <ApiErrorAlert error={error} />
      <Paper>
        <Table size="small" aria-label="Employee overrides table">
          <TableHead>
            <TableRow>
              <TableCell>Employee</TableCell><TableCell>Plan version</TableCell><TableCell>Effective</TableCell>
              <TableCell>Reason</TableCell>{canPrepare && <TableCell />}
            </TableRow>
          </TableHead>
          <TableBody>
            {data.length === 0 && <TableRow><TableCell colSpan={canPrepare ? 5 : 4}>No overrides.</TableCell></TableRow>}
            {data.map((o) => (
              <TableRow key={o.id}>
                <TableCell>{o.employee.full_name}</TableCell>
                <TableCell>
                  {planTitle(o.plan_version)}
                  {o.plan_version.calculation_model === "LEGACY_WEIGHTED" && (
                    <Chip size="small" variant="outlined" sx={{ ml: 1 }} label="Legacy (0–100) assignment" />
                  )}
                </TableCell>
                <TableCell>{period(o.effective_from, o.effective_to)}</TableCell>
                <TableCell>{o.reason || "—"}</TableCell>
                {canPrepare && (
                  <TableCell>
                    <Button size="small" onClick={() => { setEnding(o); setEndOpen(true); }} aria-label={`End override for ${o.employee.full_name}`}>End</Button>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>
      <AddOverrideDialog open={adding} onClose={() => setAdding(false)} />
      <EndDialog
        open={endOpen}
        title={ending ? `End the override for ${ending.employee.full_name}` : ""}
        withReason
        run={(body) => cfgApi.endOverride((ending as KpiCfgOverride).id, body)}
        onClose={() => setEndOpen(false)}
      />
    </Stack>
  );
}

/** Find an employee by name, email or code (the existing employee search), then pick one. */
function EmployeePicker({ value, onChange, label, error }: {
  value: string; onChange: (id: string) => void; label: string; error?: string;
}) {
  const [search, setSearch] = useState("");
  const { data } = useQuery({
    queryKey: ["employees", { search, page: 1 }],
    queryFn: () => employeesApi.list({ search: search.trim() || undefined, page: 1 }),
  });
  const people = data?.results ?? [];
  return (
    <Stack direction="row" spacing={2}>
      <TextField size="small" label={`Find ${label.toLowerCase()}`} value={search} onChange={(e) => setSearch(e.target.value)} sx={{ flex: 1 }}
        helperText="Name, email or employee code" />
      <TextField size="small" select label={label} value={value} onChange={(e) => onChange(String(e.target.value))} sx={{ flex: 1 }}
        error={!!error} helperText={error ?? (data && data.count > people.length ? `First ${people.length} of ${data.count}: refine the search.` : undefined)}>
        {people.map((p) => <MenuItem key={p.id} value={String(p.id)}>{p.full_name} ({p.department.code})</MenuItem>)}
      </TextField>
    </Stack>
  );
}

function AddOverrideDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data: plans = [] } = usePlans(open);
  const active = plans.filter((p) => p.calculation_model === "KRA_POINTS" && p.status === "ACTIVE");
  const [employee, setEmployee] = useState("");
  const [plan, setPlan] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const save = useCfgMutation(() => cfgApi.createOverride({
    employee: Number(employee), plan_version: Number(plan), effective_from: from, effective_to: to || null, reason,
  }), () => onClose());
  useEffect(() => {
    if (open) { setEmployee(""); setPlan(""); setFrom(""); setTo(""); setReason(""); save.reset(); }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(save.error);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="override-title">
      <DialogTitle id="override-title">Add employee override</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {save.error instanceof ApiError && Object.keys(errors).length === 0 && <Alert severity="error">{save.error.message}</Alert>}
          {open && <EmployeePicker label="Employee" value={employee} onChange={setEmployee} error={errors.employee} />}
          <TextField size="small" select label="Plan version" value={plan} onChange={(e) => setPlan(String(e.target.value))}
            error={!!errors.plan_version} helperText={errors.plan_version ?? "Only an active KRA plan version can be assigned."}>
            {active.map((p) => <MenuItem key={p.id} value={String(p.id)}>{planTitle(p)}</MenuItem>)}
          </TextField>
          <Stack direction="row" spacing={2}>
            <TextField size="small" type="date" label="Effective from" value={from} onChange={(e) => setFrom(e.target.value)} sx={{ flex: 1 }}
              InputLabelProps={{ shrink: true }} error={!!errors.effective_from} helperText={errors.effective_from} />
            <TextField size="small" type="date" label="Effective to (optional)" value={to} onChange={(e) => setTo(e.target.value)} sx={{ flex: 1 }}
              InputLabelProps={{ shrink: true }} error={!!errors.effective_to} helperText={errors.effective_to} />
          </Stack>
          <TextField size="small" label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} multiline minRows={2}
            error={!!errors.reason} helperText={errors.reason ?? "Required."} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!employee || !plan || !from || !reason.trim() || save.isPending} onClick={() => save.mutate()}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

/** End a default or an override on a last day (the backend refuses a day before its start). */
function EndDialog({ open, title, withReason, run, onClose }: {
  open: boolean; title: string; withReason: boolean;
  run: (body: Record<string, string>) => Promise<unknown>; onClose: () => void;
}) {
  const [lastDay, setLastDay] = useState("");
  const [reason, setReason] = useState("");
  const save = useCfgMutation(() => run(withReason ? { last_day: lastDay, reason } : { last_day: lastDay }), () => onClose());
  useEffect(() => { if (open) { setLastDay(""); setReason(""); save.reset(); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(save.error);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs" aria-labelledby="end-title">
      <DialogTitle id="end-title">{title}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {save.error && !errors.last_day && <ProblemList error={save.error} />}
          <TextField size="small" type="date" label="Last day" value={lastDay} onChange={(e) => setLastDay(e.target.value)}
            InputLabelProps={{ shrink: true }} error={!!errors.last_day} helperText={errors.last_day} />
          {withReason && (
            <TextField size="small" label="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!lastDay || save.isPending} onClick={() => save.mutate()}>End</Button>
      </DialogActions>
    </Dialog>
  );
}

// --- which plan applies? ------------------------------------------------------------------------

function ResolutionCheck() {
  const [employee, setEmployee] = useState("");
  const [date, setDate] = useState(todayIST());
  const [asked, setAsked] = useState<{ employee: number; date: string } | null>(null);
  const { data, error, isFetching } = useQuery({
    queryKey: cfgKeys.resolution(asked?.employee ?? 0, asked?.date ?? ""),
    queryFn: () => cfgApi.resolve((asked as { employee: number }).employee, (asked as { date: string }).date),
    enabled: asked !== null,
  });
  return (
    <Stack component="section" aria-label="Which plan applies" spacing={1.5}>
      <Typography variant="h6" component="h2">Which plan applies?</Typography>
      <Typography variant="body2" color="text.secondary">
        Shows the backend's answer for one employee on one date. KRA months are scored with the plan of the month's last day.
      </Typography>
      <EmployeePicker label="Employee to check" value={employee} onChange={setEmployee} />
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        <TextField size="small" type="date" label="Date" value={date} onChange={(e) => setDate(e.target.value)} InputLabelProps={{ shrink: true }} />
        <Button variant="outlined" disabled={!employee || !date || isFetching}
          onClick={() => setAsked({ employee: Number(employee), date })}>Check</Button>
      </Stack>
      <ApiErrorAlert error={error} />
      {data && <ResolutionResult result={data} />}
    </Stack>
  );
}

function ResolutionResult({ result }: { result: KpiCfgResolution }) {
  const severity = result.state === "RESOLVED" ? "success" : result.state === "AMBIGUOUS_ROLE" ? "warning" : "info";
  return (
    <Alert severity={severity} aria-label="Plan check result">
      <Stack spacing={0.5}>
        <strong>{result.employee.full_name} on {formatBusinessDate(result.date)}: {RESOLUTION_STATE_LABEL[result.state] ?? result.state}</strong>
        {result.plan_version && (
          <span>
            Plan: {planTitle(result.plan_version)} — {MODEL_LABEL[result.plan_version.calculation_model] ?? result.plan_version.calculation_model}
          </span>
        )}
        {result.source && <span>Because of: {RESOLUTION_SOURCE_LABEL[result.source] ?? result.source}</span>}
        <span>{result.reason}</span>
        <span>System roles: {result.roles.length ? result.roles.join(", ") : "none"}</span>
        {result.matching_default_ids.length > 1 && (
          <span>Matching defaults: {result.matching_default_ids.length} (IDs {result.matching_default_ids.join(", ")})</span>
        )}
      </Stack>
    </Alert>
  );
}

import {
  Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, FormControlLabel, FormGroup,
  FormHelperText, FormLabel, MenuItem, Stack, TextField, Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "../../api/apiClient";
import { schedulesApi } from "../../api/endpoints";
import { PERM, type Frequency, type NonWorkingDayPolicy, type Responsibility, type ScheduleInput } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";

/** Phase A: schedule fields shared by the responsibility setup form and "Add schedule".
 * Only the existing DAILY / MONTHLY frequencies; the backend stays the authority. */

export const POLICY_LABEL: Record<NonWorkingDayPolicy, string> = {
  SKIP: "Skip non-working days", NEXT_WORKING_DAY: "Next working day", PREVIOUS_WORKING_DAY: "Previous working day",
};
const FREQUENCY_LABEL: Record<Frequency, string> = {
  DAILY: "Daily (working days)", MONTHLY: "Monthly", WEEKLY: "Weekly", ONCE: "Specific date (once)",
};
/** Phase B: 0=Monday .. 6=Sunday, the company calendar's convention. */
export const WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

export interface ScheduleFormValue {
  frequency: Frequency; runTime: string; dayOfMonth: string; policy: NonWorkingDayPolicy;
  effectiveFrom: string; effectiveTo: string;
  weekdays: number[]; runDate: string; // Phase B
}

/** Today's date in IST as YYYY-MM-DD (the backend's business date). */
export function todayIST(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

export function emptySchedule(): ScheduleFormValue {
  return {
    frequency: "DAILY", runTime: "", dayOfMonth: "", policy: "SKIP", effectiveFrom: todayIST(), effectiveTo: "",
    weekdays: [], runDate: "",
  };
}

export type ScheduleErrors = Partial<Record<
  "frequency" | "run_time" | "day_of_month" | "effective_from" | "effective_to" | "weekdays" | "run_date", string
>>;

/** Mirrors the backend checks so the user sees problems before saving. Only Admin may start
 * a schedule in the past (Phase A rule); HR and Operations Managers start today or later. */
export function validateSchedule(value: ScheduleFormValue, allowPast: boolean): ScheduleErrors {
  const errors: ScheduleErrors = {};
  if (!value.runTime) errors.run_time = "Choose the time (IST).";
  if (value.frequency === "MONTHLY") {
    const day = Number(value.dayOfMonth);
    if (!Number.isInteger(day) || day < 1 || day > 28) errors.day_of_month = "Use a day from 1 to 28.";
  }
  if (value.frequency === "WEEKLY" && value.weekdays.length === 0) errors.weekdays = "Choose at least one weekday.";
  if (value.frequency === "ONCE") {
    // Phase B: one-off dates are today or later for every role; the server derives the window.
    if (!value.runDate) errors.run_date = "Choose the date.";
    else if (value.runDate < todayIST()) errors.run_date = "Choose today or a later date.";
    return errors;
  }
  if (!value.effectiveFrom) errors.effective_from = "Choose the start date.";
  else if (!allowPast && value.effectiveFrom < todayIST()) errors.effective_from = "A schedule cannot start in the past.";
  if (value.effectiveTo && value.effectiveFrom && value.effectiveTo < value.effectiveFrom) {
    errors.effective_to = "Must be on or after the start.";
  }
  return errors;
}

export function toScheduleInput(value: ScheduleFormValue): ScheduleInput {
  if (value.frequency === "ONCE") {
    return {
      frequency: "ONCE", run_time: value.runTime, day_of_month: null, run_date: value.runDate,
      non_working_day_policy: value.policy,
    };
  }
  if (value.frequency === "WEEKLY") {
    return {
      frequency: "WEEKLY", run_time: value.runTime, day_of_month: null, weekdays: [...value.weekdays].sort((a, b) => a - b),
      non_working_day_policy: value.policy, effective_from: value.effectiveFrom, effective_to: value.effectiveTo || null,
    };
  }
  return { // DAILY / MONTHLY: exactly the Phase A payload
    frequency: value.frequency,
    run_time: value.runTime,
    day_of_month: value.frequency === "MONTHLY" ? Number(value.dayOfMonth) : null,
    non_working_day_policy: value.policy,
    effective_from: value.effectiveFrom,
    effective_to: value.effectiveTo || null,
  };
}

/** Controlled schedule fields. `errors` combines client checks and backend field errors. */
export function ScheduleFields({ value, onChange, errors = {}, allowPast }: {
  value: ScheduleFormValue; onChange: (next: ScheduleFormValue) => void; errors?: ScheduleErrors; allowPast: boolean;
}) {
  const set = <K extends keyof ScheduleFormValue>(key: K, v: ScheduleFormValue[K]) => onChange({ ...value, [key]: v });
  return (
    <Stack spacing={2} component="fieldset" sx={{ border: 0, p: 0, m: 0 }} aria-label="Schedule">
      <TextField size="small" select label="Repeats" value={value.frequency}
        onChange={(e) => set("frequency", e.target.value as Frequency)} error={!!errors.frequency} helperText={errors.frequency}>
        {(Object.keys(FREQUENCY_LABEL) as Frequency[]).map((f) => <MenuItem key={f} value={f}>{FREQUENCY_LABEL[f]}</MenuItem>)}
      </TextField>
      <TextField size="small" label="Time (IST)" type="time" value={value.runTime} onChange={(e) => set("runTime", e.target.value)}
        InputLabelProps={{ shrink: true }} error={!!errors.run_time} helperText={errors.run_time} />
      {value.frequency === "MONTHLY" && (
        <TextField size="small" label="Day of month (1–28)" type="number" value={value.dayOfMonth}
          onChange={(e) => set("dayOfMonth", e.target.value)} error={!!errors.day_of_month} helperText={errors.day_of_month} />
      )}
      {value.frequency === "WEEKLY" && (
        <WeekdayPicker value={value.weekdays} onChange={(next) => set("weekdays", next)} error={errors.weekdays} />
      )}
      {value.frequency === "ONCE" && (
        <TextField size="small" label="Date" type="date" value={value.runDate} onChange={(e) => set("runDate", e.target.value)}
          InputLabelProps={{ shrink: true }} inputProps={{ min: todayIST() }}
          error={!!errors.run_date} helperText={errors.run_date ?? "Generated once, on this date."} />
      )}
      <TextField size="small" select label="On a non-working day" value={value.policy}
        onChange={(e) => set("policy", e.target.value as NonWorkingDayPolicy)}>
        {(Object.keys(POLICY_LABEL) as NonWorkingDayPolicy[]).map((p) => <MenuItem key={p} value={p}>{POLICY_LABEL[p]}</MenuItem>)}
      </TextField>
      {value.frequency !== "ONCE" && <Stack direction="row" spacing={2}>
        <TextField size="small" label="Starts on" type="date" value={value.effectiveFrom} onChange={(e) => set("effectiveFrom", e.target.value)}
          InputLabelProps={{ shrink: true }} inputProps={allowPast ? {} : { min: todayIST() }}
          error={!!errors.effective_from} helperText={errors.effective_from} />
        <TextField size="small" label="Ends on (optional)" type="date" value={value.effectiveTo} onChange={(e) => set("effectiveTo", e.target.value)}
          InputLabelProps={{ shrink: true }} error={!!errors.effective_to} helperText={errors.effective_to} />
      </Stack>}
    </Stack>
  );
}

/** Phase B: the weekdays of a WEEKLY schedule (at least one). */
export function WeekdayPicker({ value, onChange, error }: { value: number[]; onChange: (next: number[]) => void; error?: string }) {
  const toggle = (day: number) => onChange(value.includes(day) ? value.filter((d) => d !== day) : [...value, day].sort((a, b) => a - b));
  return (
    <FormControl component="fieldset" error={!!error}>
      <FormLabel component="legend">Weekdays</FormLabel>
      <FormGroup row>
        {WEEKDAY_SHORT.map((label, day) => (
          <FormControlLabel key={label} label={label}
            control={<Checkbox size="small" checked={value.includes(day)} onChange={() => toggle(day)} />} />
        ))}
      </FormGroup>
      {error && <FormHelperText>{error}</FormHelperText>}
    </FormControl>
  );
}

/** Backend field errors for one section ("schedule.run_time" -> run_time), or for a plain form. */
export function serverErrors(error: unknown, prefix = ""): Record<string, string> {
  if (!(error instanceof ApiError)) return {};
  const out: Record<string, string> = {};
  for (const [key, messages] of Object.entries(error.fields ?? {})) {
    if (!prefix) out[key] = messages.join(" ");
    else if (key.startsWith(`${prefix}.`)) out[key.slice(prefix.length + 1)] = messages.join(" ");
  }
  return out;
}

/** Add a schedule to an existing responsibility (e.g. one created before Phase A). */
export function AddScheduleDialog({ responsibility, onClose }: { responsibility: Responsibility | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { hasPerm } = useAuth();
  const allowPast = hasPerm(PERM.manageSchedules);
  const [title, setTitle] = useState("");
  const [value, setValue] = useState<ScheduleFormValue>(emptySchedule());
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    if (!responsibility) return;
    setTitle(responsibility.name); setValue(emptySchedule()); setChecked(false);
  }, [responsibility]);
  const mutation = useMutation({
    mutationFn: () => schedulesApi.create({ ...toScheduleInput(value), responsibility: (responsibility as Responsibility).id, title }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["responsibilities"] }),
        qc.invalidateQueries({ queryKey: ["recurring-schedules"] }),
      ]);
      onClose();
    },
  });
  const clientErrors = checked ? validateSchedule(value, allowPast) : {};
  const backend = serverErrors(mutation.error);
  const errors: ScheduleErrors = { ...backend, ...clientErrors };
  const save = () => {
    setChecked(true);
    if (!title.trim() || Object.keys(validateSchedule(value, allowPast)).length > 0) return;
    mutation.mutate();
  };
  const apiError = mutation.error instanceof ApiError ? mutation.error : null;
  return (
    <Dialog open={responsibility !== null} onClose={onClose} fullWidth maxWidth="xs" aria-labelledby="add-schedule-title">
      <DialogTitle id="add-schedule-title">Add schedule — {responsibility?.name}</DialogTitle>
      <DialogContent><Stack spacing={2} sx={{ mt: 1 }}>
        {apiError && <Alert severity="error">{apiError.message}</Alert>}
        <Typography variant="body2" color="text.secondary">Tasks are generated automatically from this schedule for the owner on each date.</Typography>
        <TextField size="small" label="Schedule title" value={title} onChange={(e) => setTitle(e.target.value)}
          error={checked && !title.trim()} helperText={checked && !title.trim() ? "Enter a title." : backend.title} />
        <ScheduleFields value={value} onChange={setValue} errors={errors} allowPast={allowPast} />
      </Stack></DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={mutation.isPending} onClick={save}>Add schedule</Button>
      </DialogActions>
    </Dialog>
  );
}

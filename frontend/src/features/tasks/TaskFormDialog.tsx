import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Checkbox, Dialog,
  DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem,
  TextField, Typography,
} from "@mui/material";
import Grid from "@mui/material/Grid2";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "../../api/apiClient";
import { departmentsApi, tasksApi } from "../../api/endpoints";
import {
  RECEIVED_SOURCES, TASK_PRIORITIES, type ReceivedSource, type TaskDetail, type TaskPriority,
} from "../../api/types";
import { DateTimeText } from "../../components/DateTimeText";
import { TASK_PRIORITY_LABEL } from "./labels";

interface Props { open: boolean; onClose: () => void; onCreated: (task: TaskDetail) => void; }

/** The form collects IST wall-clock input; the server stores UTC and calculates every SLA value.
 * Change Set 1 (D5): no task type — the SLA comes from the priority (template is always null). */
function istToIso(local: string): string | null {
  return local ? `${local}:00+05:30` : null;
}

const ADVANCED_FIELDS = ["received_at", "received_at_source"];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Box component="section" aria-label={title}>
      <Typography variant="overline" color="text.secondary" component="h2">{title}</Typography>
      <Grid container spacing={1.5}>{children}</Grid>
    </Box>
  );
}

export function TaskFormDialog({ open, onClose, onCreated }: Props) {
  const qc = useQueryClient();
  const { data: assignees = [] } = useQuery({ queryKey: ["task-assignees"], queryFn: tasksApi.assignees, enabled: open });
  const { data: departments = [] } = useQuery({ queryKey: ["departments"], queryFn: departmentsApi.list, enabled: open });
  const { data: categories = [] } = useQuery({ queryKey: ["task-categories"], queryFn: tasksApi.categories, enabled: open });

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<TaskPriority>("MEDIUM");
  const [assignee, setAssignee] = useState("");
  const [department, setDepartment] = useState("");
  const [category, setCategory] = useState("");
  const [ackRequired, setAckRequired] = useState(false);
  const [verificationRequired, setVerificationRequired] = useState(false);
  const [receivedAt, setReceivedAt] = useState("");
  const [source, setSource] = useState<ReceivedSource | "">("");
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTitle(""); setDescription(""); setPriority("MEDIUM");
    setDepartment(""); setCategory("");
    setAckRequired(false); setVerificationRequired(false); setReceivedAt(""); setSource(""); setAdvanced(false);
  }, [open]);
  useEffect(() => {
    if (open && assignees.length === 1) setAssignee(String(assignees[0].id));
  }, [open, assignees]);

  const previewInput = {
    template: null,
    assigned_to: assignee ? Number(assignee) : null,
    acknowledgment_required: ackRequired,
    trigger_at: null,
    priority,
  };
  const { data: preview } = useQuery({
    queryKey: ["sla-preview", previewInput],
    queryFn: () => tasksApi.slaPreview(previewInput),
    enabled: open,
  });

  const mutation = useMutation({
    mutationFn: () => tasksApi.create({
      title, description, priority, assigned_to: Number(assignee),
      department: Number(department), category: Number(category),
      template: null,
      trigger_at: null,
      received_at: istToIso(receivedAt), received_at_source: source || null,
      acknowledgment_required: ackRequired,
      verification_required: verificationRequired,
    }),
    onSuccess: async (task) => {
      await qc.invalidateQueries({ queryKey: ["tasks"] });
      onCreated(task);
    },
    onError: (error) => {
      if (error instanceof ApiError && ADVANCED_FIELDS.some((f) => error.fields[f])) setAdvanced(true);
    },
  });
  const error = mutation.error instanceof ApiError ? mutation.error : null;
  const fieldError = (name: string) => error?.fields?.[name]?.join(" ");
  const errorProps = (name: string) => ({ error: !!fieldError(name), helperText: fieldError(name), size: "small" as const });
  const resolution = preview?.resolution ?? null;
  const ack = preview?.acknowledgment ?? null;

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md" aria-labelledby="task-form-title">
      <DialogTitle id="task-form-title">New task</DialogTitle>
      <DialogContent dividers sx={{ display: "grid", gap: 1.5 }}>
        {error && (
          <Alert severity="error" role="alert">
            {error.message}
            {fieldError("non_field_errors") ? ` ${fieldError("non_field_errors")}` : ""}
          </Alert>
        )}

        <Section title="Basic information">
          <Grid size={12}>
            <TextField fullWidth autoFocus label="Title" value={title} onChange={(e) => setTitle(e.target.value)} {...errorProps("title")} />
          </Grid>
          <Grid size={12}>
            <TextField fullWidth label="Description" value={description} onChange={(e) => setDescription(e.target.value)} multiline minRows={1} maxRows={4} {...errorProps("description")} />
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <TextField select fullWidth label="Priority" value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}
              {...errorProps("priority")} helperText={fieldError("priority") ?? "Sets the SLA."}>
              {TASK_PRIORITIES.map((p) => <MenuItem key={p} value={p}>{TASK_PRIORITY_LABEL[p]}</MenuItem>)}
            </TextField>
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <TextField select fullWidth required label="Department" value={department}
              onChange={(e) => setDepartment(String(e.target.value))} {...errorProps("department")}>
              {departments.map((d) => <MenuItem key={d.id} value={String(d.id)}>{d.code} — {d.name}</MenuItem>)}
            </TextField>
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <TextField select fullWidth required label="Category" value={category}
              onChange={(e) => setCategory(String(e.target.value))} {...errorProps("category")}>
              {categories.map((c) => <MenuItem key={c.id} value={String(c.id)}>{c.name}</MenuItem>)}
            </TextField>
          </Grid>
        </Section>

        <Section title="Assignment">
          <Grid size={12}>
            <TextField select fullWidth label="Assign to" value={assignee} onChange={(e) => setAssignee(String(e.target.value))}
              disabled={assignees.length <= 1} {...errorProps("assigned_to")}
              helperText={fieldError("assigned_to") ?? "The task's department is chosen above; it does not follow the assignee."}>
              {assignees.map((a) => <MenuItem key={a.id} value={String(a.id)}>{a.full_name} ({a.department.code})</MenuItem>)}
            </TextField>
          </Grid>
        </Section>

        <Section title="Configuration">
          <Grid size={{ xs: 12, sm: 6 }}>
            <FormControlLabel control={<Checkbox size="small" checked={ackRequired} onChange={(e) => setAckRequired(e.target.checked)} />}
              label={ackRequired && ack?.duration_minutes ? `Acknowledgement required — ${ack.duration_minutes / 60} hours` : "Acknowledgement required"} />
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <FormControlLabel control={<Checkbox size="small" checked={verificationRequired} onChange={(e) => setVerificationRequired(e.target.checked)} />} label="Verification required" />
          </Grid>
          {(fieldError("acknowledgment_required") || fieldError("verification_required")) && (
            <Grid size={12}>
              <Typography variant="caption" color="error">{fieldError("acknowledgment_required") ?? fieldError("verification_required")}</Typography>
            </Grid>
          )}
        </Section>

        <Box component="section" aria-label="SLA" sx={{ p: 1.5, borderRadius: 1, bgcolor: "action.hover" }}>
          <Typography variant="overline" color="text.secondary" component="h2">SLA</Typography>
          {resolution ? (
            <Typography variant="body2" component="div">
              <strong>{resolution.rule_name}</strong>
              {resolution.duration_minutes ? ` · ${resolution.duration_minutes / 60} hours` : ""}
              <br />
              {resolution.waiting_for ?? <>Starts: <DateTimeText value={resolution.start_at} /> · Due: <strong><DateTimeText value={resolution.due_at} /></strong></>}
            </Typography>
          ) : (
            <Typography variant="body2">{preview?.resolution_note ?? "No SLA configured"}</Typography>
          )}
          <Typography variant="caption" color="text.secondary">Calculated by the system. The due time cannot be changed.</Typography>
        </Box>

        <Accordion expanded={advanced} onChange={(_, value) => setAdvanced(value)} disableGutters>
          <AccordionSummary expandIcon={<ExpandMoreIcon />}>Advanced details</AccordionSummary>
          <AccordionDetails>
            <Grid container spacing={2}>
              <Grid size={{ xs: 12, sm: 6 }}>
                <TextField fullWidth label="Actual received time (IST)" type="datetime-local" value={receivedAt}
                  onChange={(e) => setReceivedAt(e.target.value)} InputLabelProps={{ shrink: true }} {...errorProps("received_at")} />
              </Grid>
              <Grid size={{ xs: 12, sm: 6 }}>
                <TextField select fullWidth label="Received via" value={source} onChange={(e) => setSource(e.target.value as ReceivedSource | "")} {...errorProps("received_at_source")}>
                  <MenuItem value="">Not recorded</MenuItem>
                  {RECEIVED_SOURCES.map((s) => <MenuItem key={s} value={s}>{s.charAt(0) + s.slice(1).toLowerCase()}</MenuItem>)}
                </TextField>
              </Grid>
            </Grid>
          </AccordionDetails>
        </Accordion>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!title.trim() || !assignee || !department || !category || mutation.isPending} onClick={() => mutation.mutate()}>
          {mutation.isPending ? "Creating…" : "Create Task"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

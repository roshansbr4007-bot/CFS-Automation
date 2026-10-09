import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import {
  Alert, Box, Button, Card, CardContent, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  Link, MenuItem, Stack, TextField, Typography,
} from "@mui/material";
import Grid from "@mui/material/Grid2";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ChangeEvent, type ReactNode } from "react";
import { Link as RouterLink, useLocation, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../../api/apiClient";
import { tasksApi } from "../../api/endpoints";
import type { TaskAction, TaskDetail } from "../../api/types";
import { DateTimeText, formatBusinessDate, formatIST } from "../../components/DateTimeText";
import { acknowledgmentLabel, TASK_PRIORITY_LABEL, STATUS_LABEL, VERIFICATION_LABEL } from "./labels";
import { SlaBadge } from "./SlaBadge";
import { SlaPanel } from "./SlaPanel";
import { SourceBadge } from "./SourceBadge";

type Prompt = "block" | "cancel" | "verify" | "reject_verification" | "reassign";

const DIRECT: { action: TaskAction; label: string; endpoint: string }[] = [
  { action: "acknowledge", label: "Acknowledge", endpoint: "acknowledge" },
  { action: "start", label: "Start", endpoint: "start" },
  { action: "complete", label: "Complete Task", endpoint: "complete" },
  { action: "unblock", label: "Resume", endpoint: "unblock" },
];
const PROMPTED: { action: Prompt; label: string }[] = [
  { action: "block", label: "Put on hold" },
  { action: "verify", label: "Verify" },
  { action: "reject_verification", label: "Reject verification" },
  { action: "reassign", label: "Reassign" },
  { action: "cancel", label: "Cancel task" },
];
const PROMPT_TITLE: Record<Prompt, string> = {
  block: "Put task on hold", cancel: "Cancel task", verify: "Verify completion",
  reject_verification: "Reject verification", reassign: "Reassign task",
};

export function errorMessage(error: unknown): string | null {
  if (!(error instanceof ApiError)) return error ? "Something went wrong. Try again." : null;
  if (error.status === 409 && error.code === "version_conflict") {
    return "This task was changed by someone else. It has been reloaded — check it and try again.";
  }
  const fields = Object.values(error.fields ?? {}).flat().join(" ");
  return fields ? `${error.message} ${fields}` : error.message;
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Stack direction="row" spacing={1} sx={{ py: 0.4 }}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 130 }}>{label}</Typography>
      <Typography variant="body2" component="div">{children}</Typography>
    </Stack>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card variant="outlined" component="section" aria-label={title}>
      <CardContent>
        <Typography variant="overline" color="text.secondary" component="h2">{title}</Typography>
        {children}
      </CardContent>
    </Card>
  );
}

export function TaskDetailPage() {
  const id = Number(useParams().id);
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { data: task, error: loadError } = useQuery({ queryKey: ["task", id], queryFn: () => tasksApi.get(id) });
  const { data: comments = [] } = useQuery({ queryKey: ["task-comments", id], queryFn: () => tasksApi.comments(id) });
  const { data: attachments = [] } = useQuery({ queryKey: ["task-attachments", id], queryFn: () => tasksApi.attachments(id) });
  const allowed = new Set(task?.allowed_actions ?? []);
  const { data: assignees = [] } = useQuery({ queryKey: ["task-assignees"], queryFn: tasksApi.assignees, enabled: allowed.has("reassign") });

  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [reason, setReason] = useState("");
  const [remarks, setRemarks] = useState("");
  const [newAssignee, setNewAssignee] = useState("");
  const [comment, setComment] = useState("");
  const [notice, setNotice] = useState<string | null>(
    (location.state as { created?: string } | null)?.created ? `Task ${(location.state as { created: string }).created} created.` : null,
  );

  const refresh = () => Promise.all([
    qc.invalidateQueries({ queryKey: ["task", id] }),
    qc.invalidateQueries({ queryKey: ["tasks"] }),
  ]);
  const closePrompt = () => { setPrompt(null); setReason(""); setRemarks(""); setNewAssignee(""); };

  const act = useMutation({
    mutationFn: ({ endpoint, body }: { endpoint: string; body: Record<string, unknown> }) =>
      tasksApi.action(id, endpoint, { version: (task as TaskDetail).version, ...body }),
    onSuccess: async (updated, { endpoint }) => {
      closePrompt();
      qc.setQueryData(["task", id], updated);
      if (endpoint === "complete" && updated.completed_at) {
        setNotice(`Completed at ${formatIST(updated.completed_at)} IST (recorded by the server).`);
      }
      await refresh();
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.code === "version_conflict") { closePrompt(); await refresh(); }
    },
  });
  const remove = useMutation({
    mutationFn: () => tasksApi.remove(id, (task as TaskDetail).version),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["tasks"] });
      navigate("/tasks", { state: { deleted: (task as TaskDetail).reference } });
    },
    onError: async (error) => {
      setConfirmDelete(false);
      if (error instanceof ApiError && error.code === "version_conflict") await refresh();
    },
  });
  const addComment = useMutation({
    mutationFn: () => tasksApi.addComment(id, comment),
    onSuccess: async () => { setComment(""); await qc.invalidateQueries({ queryKey: ["task-comments", id] }); },
  });
  const upload = useMutation({
    mutationFn: (file: File) => tasksApi.upload(id, file),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["task-attachments", id] }),
  });

  const submitPrompt = () => {
    if (prompt === "block" || prompt === "cancel") act.mutate({ endpoint: prompt, body: { reason } });
    if (prompt === "verify") act.mutate({ endpoint: "verify", body: { remarks } });
    if (prompt === "reject_verification") act.mutate({ endpoint: "reject-verification", body: { reason, remarks } });
    if (prompt === "reassign") act.mutate({ endpoint: "reassign", body: { assigned_to: Number(newAssignee), note: remarks } });
  };
  const promptReady = prompt === "block" || prompt === "cancel" ? !!reason.trim()
    : prompt === "reject_verification" ? !!reason.trim() && !!remarks.trim()
      : prompt === "reassign" ? !!newAssignee : prompt === "verify";
  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) upload.mutate(file);
    event.target.value = "";
  };

  if (loadError) return <Alert severity="error" role="alert">{errorMessage(loadError)}</Alert>;
  if (!task) return <Typography>Loading…</Typography>;

  return (
    <Stack spacing={2.5}>
      <Box>
        <Link component={RouterLink} to="/tasks" underline="hover" sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}>
          <ArrowBackIcon fontSize="small" /> Tasks
        </Link>
        <Typography variant="h2" component="h1" sx={{ mt: 1 }}>{task.title}</Typography>
        <Stack direction="row" spacing={1} sx={{ mt: 1, alignItems: "center", flexWrap: "wrap" }}>
          <Typography variant="body2" color="text.secondary">{task.reference}</Typography>
          <Chip size="small" label={STATUS_LABEL[task.status]} color={task.status === "BLOCKED" ? "warning" : "default"} />
          <SlaBadge clock={task.sla.resolution} note={task.sla.resolution_note} />
          {task.verification_status !== "NOT_REQUIRED" && <Chip size="small" variant="outlined" label={VERIFICATION_LABEL[task.verification_status]} />}
          {task.rework_count > 0 && <Chip size="small" variant="outlined" label={`Rework ${task.rework_count}`} />}
        </Stack>
      </Box>

      {notice && <Alert severity="success" onClose={() => setNotice(null)}>{notice}</Alert>}
      {act.error && <Alert severity="warning" role="alert">{errorMessage(act.error)}</Alert>}
      {remove.error && <Alert severity="warning" role="alert">{errorMessage(remove.error)}</Alert>}

      <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 1 }} aria-label="Task actions">
        {DIRECT.filter((a) => allowed.has(a.action)).map((a) => (
          <Button key={a.action} variant="contained" disabled={act.isPending} onClick={() => act.mutate({ endpoint: a.endpoint, body: {} })}>{a.label}</Button>
        ))}
        {PROMPTED.filter((a) => allowed.has(a.action)).map((a) => (
          <Button key={a.action} variant="outlined" disabled={act.isPending} onClick={() => setPrompt(a.action)}>{a.label}</Button>
        ))}
        {allowed.has("delete") && (
          <Button color="error" variant="outlined" disabled={remove.isPending} onClick={() => setConfirmDelete(true)}>Delete task</Button>
        )}
      </Stack>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Stack spacing={2}>
            <Panel title="Assignment">
              <Row label="Assigned to">{task.assigned_to.full_name}</Row>
              <Row label="Created by">{task.created_by.full_name || task.created_by.email}</Row>
              <Row label="Department">{task.department.code} — {task.department.name}</Row>
              <Row label="Category">{task.category?.name ?? "—"}</Row>
              <Row label="Source"><SourceBadge source={task.source} /></Row>
              {task.source === "SCHEDULED" && (
                <>
                  <Row label="Responsibility">{task.responsibility?.name ?? "—"}</Row>
                  <Row label="Schedule">{task.schedule?.title ?? "—"}</Row>
                  <Row label="Occurrence date">{task.occurrence_date ? formatBusinessDate(task.occurrence_date) : "—"}</Row>
                  <Row label="Generated at"><DateTimeText value={task.generated_at} /></Row>
                </>
              )}
              <Row label="Priority">{TASK_PRIORITY_LABEL[task.priority]}</Row>
              <Row label="Task type">{task.template?.name ?? "Ad-hoc"}</Row>
              <Row label="Acknowledgement">{acknowledgmentLabel(task)}</Row>
              <Row label="Verification">{VERIFICATION_LABEL[task.verification_status]}</Row>
            </Panel>
            <Panel title="Timeline">
              <Row label="Created"><DateTimeText value={task.created_at} /></Row>
              <Row label="Received">{task.received_at ? <><DateTimeText value={task.received_at} /> via {task.received_at_source}</> : "Not recorded"}</Row>
              {task.trigger_at && <Row label="Event"><DateTimeText value={task.trigger_at} /></Row>}
              <Row label="Acknowledged"><DateTimeText value={task.acknowledged_at} /></Row>
              <Row label="Started"><DateTimeText value={task.started_at} /></Row>
              <Row label="Due"><DateTimeText value={task.sla.resolution?.due_at} /></Row>
              <Row label="Completed at">{task.completed_at ? <><DateTimeText value={task.completed_at} /> IST</> : "—"}</Row>
              {task.status === "BLOCKED" && <Row label="On hold because">{task.blocked_reason}</Row>}
              {task.status === "CANCELLED" && <Row label="Cancelled because">{task.cancelled_reason}</Row>}
            </Panel>
          </Stack>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <Stack spacing={2}>
            <Panel title="SLA"><SlaPanel sla={task.sla} /></Panel>
            {task.description && <Panel title="Description"><Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{task.description}</Typography></Panel>}
          </Stack>
        </Grid>
      </Grid>

      <Panel title="Verification history">
        {task.verifications.length === 0 ? <Typography variant="body2" color="text.secondary">No verification decisions yet.</Typography> : task.verifications.map((v) => (
          <Typography key={v.cycle_no} variant="body2">
            Cycle {v.cycle_no}: <strong>{v.decision === "VERIFIED" ? "Verified" : "Rejected"}</strong> by {v.decided_by.full_name || v.decided_by.email}, <DateTimeText value={v.decided_at} />
            {v.rejection_reason && <> — {v.rejection_reason}</>}{v.remarks && <> ({v.remarks})</>}
          </Typography>
        ))}
      </Panel>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 7 }}>
          <Panel title="Comments">
            {comments.length === 0 && <Typography variant="body2" color="text.secondary">No comments yet.</Typography>}
            {comments.map((c) => (
              <Box key={c.id} sx={{ mb: 1 }}>
                <Typography variant="caption" color="text.secondary">{c.author.full_name || c.author.email} · <DateTimeText value={c.created_at} /></Typography>
                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{c.body}</Typography>
              </Box>
            ))}
            {allowed.has("comment") && (
              <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                <TextField fullWidth size="small" label="Add a comment" value={comment} onChange={(e) => setComment(e.target.value)} multiline />
                <Button variant="outlined" disabled={!comment.trim() || addComment.isPending} onClick={() => addComment.mutate()}>Post</Button>
              </Stack>
            )}
          </Panel>
        </Grid>
        <Grid size={{ xs: 12, md: 5 }}>
          <Panel title="Attachments">
            {upload.error && <Alert severity="error" role="alert">{errorMessage(upload.error)}</Alert>}
            {attachments.length === 0 && <Typography variant="body2" color="text.secondary">No attachments.</Typography>}
            <Stack spacing={0.5}>
              {attachments.map((a) => <Link key={a.id} href={tasksApi.downloadUrl(task.id, a.id)}>{a.original_filename}</Link>)}
            </Stack>
            {allowed.has("attach") && (
              <Button component="label" size="small" variant="outlined" sx={{ mt: 1 }} disabled={upload.isPending}>
                Attach file
                <input hidden type="file" accept=".pdf,.png,.jpg,.jpeg,.xlsx,.eml" onChange={onFile} />
              </Button>
            )}
          </Panel>
        </Grid>
      </Grid>

      <Dialog open={confirmDelete} onClose={() => setConfirmDelete(false)} maxWidth="xs" aria-labelledby="task-delete-title">
        <DialogTitle id="task-delete-title">Delete this task permanently?</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            {task.reference} and its comments, attachments, assignment history and SLA records will be removed.
            The deletion is recorded in the audit log. This cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDelete(false)}>Keep task</Button>
          <Button color="error" variant="contained" disabled={remove.isPending} onClick={() => remove.mutate()}>Delete permanently</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={prompt !== null} onClose={closePrompt} fullWidth maxWidth="xs" aria-labelledby="task-prompt-title">
        <DialogTitle id="task-prompt-title">{prompt ? PROMPT_TITLE[prompt] : ""}</DialogTitle>
        <DialogContent sx={{ display: "grid", gap: 2, pt: "8px !important" }}>
          {prompt === "reassign" && (
            <TextField select label="New assignee" value={newAssignee} onChange={(e) => setNewAssignee(e.target.value)}>
              {assignees.filter((a) => a.id !== task.assigned_to.id).map((a) => <MenuItem key={a.id} value={a.id}>{a.full_name} ({a.department.code})</MenuItem>)}
            </TextField>
          )}
          {(prompt === "block" || prompt === "cancel" || prompt === "reject_verification") && (
            <TextField label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} required />
          )}
          {(prompt === "verify" || prompt === "reject_verification" || prompt === "reassign") && (
            <TextField label={prompt === "reassign" ? "Note" : "Remarks"} value={remarks} onChange={(e) => setRemarks(e.target.value)} required={prompt === "reject_verification"} multiline />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={closePrompt}>Back</Button>
          <Button variant="contained" disabled={!promptReady || act.isPending} onClick={submitPrompt}>Confirm</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}

import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider,
  Link, MenuItem, Stack, TextField, Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ChangeEvent, type ReactNode } from "react";

import { ApiError } from "../../api/apiClient";
import { tasksApi } from "../../api/endpoints";
import type { TaskAction, TaskDetail } from "../../api/types";
import { DateTimeText } from "../../components/DateTimeText";
import { acknowledgmentLabel, PRIORITY_LABEL, STATUS_LABEL, VERIFICATION_LABEL } from "./labels";

type Prompt = "block" | "cancel" | "verify" | "reject_verification" | "reassign" | "complete" | null;

const SIMPLE_ACTIONS: { action: TaskAction; label: string; endpoint: string }[] = [
  { action: "acknowledge", label: "Acknowledge", endpoint: "acknowledge" },
  { action: "start", label: "Start", endpoint: "start" },
  { action: "unblock", label: "Resume", endpoint: "unblock" },
];
const WORK_RESPONSE_MAX = 5000;
const PROMPT_ACTIONS: { action: Exclude<Prompt, null>; label: string }[] = [
  { action: "block", label: "Put on hold" },
  { action: "reassign", label: "Reassign" },
  { action: "verify", label: "Verify" },
  { action: "reject_verification", label: "Reject verification" },
  { action: "cancel", label: "Cancel task" },
];

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Stack direction="row" spacing={1}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 150 }}>{label}</Typography>
      <Typography variant="body2" component="div">{children}</Typography>
    </Stack>
  );
}

export function conflictMessage(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  if (error.status === 409 && error.code === "version_conflict") {
    return "This task was changed by someone else. It has been reloaded — check it and try again.";
  }
  return error.message;
}

export function TaskDetailDialog({ taskId, onClose }: { taskId: number | null; onClose: () => void }) {
  const qc = useQueryClient();
  const open = taskId !== null;
  const { data: task, error: loadError } = useQuery({
    queryKey: ["task", taskId], queryFn: () => tasksApi.get(taskId!), enabled: open,
  });
  const { data: comments = [] } = useQuery({
    queryKey: ["task-comments", taskId], queryFn: () => tasksApi.comments(taskId!), enabled: open,
  });
  const { data: attachments = [] } = useQuery({
    queryKey: ["task-attachments", taskId], queryFn: () => tasksApi.attachments(taskId!), enabled: open,
  });
  const allowed = new Set(task?.allowed_actions ?? []);
  const { data: assignees = [] } = useQuery({
    queryKey: ["task-assignees"], queryFn: tasksApi.assignees, enabled: open && allowed.has("reassign"),
  });

  const [prompt, setPrompt] = useState<Prompt>(null);
  const [reason, setReason] = useState("");
  const [remarks, setRemarks] = useState("");
  const [newAssignee, setNewAssignee] = useState("");
  const [comment, setComment] = useState("");
  const [draft, setDraft] = useState<{ taskId: number | null; text: string }>({ taskId, text: "" });
  const workResponse = draft.taskId === taskId ? draft.text : "";
  const setWorkResponse = (text: string) => setDraft({ taskId, text });
  const responseLength = Array.from(workResponse.trim()).length;

  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["task", taskId] }),
      qc.invalidateQueries({ queryKey: ["tasks"] }),
    ]);
  };
  const closePrompt = () => { setPrompt(null); setReason(""); setRemarks(""); setNewAssignee(""); };

  const act = useMutation({
    mutationFn: ({ endpoint, body }: { endpoint: string; body: Record<string, unknown> }) =>
      tasksApi.action(taskId!, endpoint, { version: (task as TaskDetail).version, ...body }),
    onSuccess: async (_updated, { endpoint }) => {
      if (endpoint === "complete") {
        setWorkResponse("");
        await qc.invalidateQueries({ queryKey: ["task-comments", taskId] });
      }
      closePrompt();
      await refresh();
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.code === "version_conflict") { closePrompt(); await refresh(); }
    },
  });
  const addComment = useMutation({
    mutationFn: () => tasksApi.addComment(taskId!, comment),
    onSuccess: async () => { setComment(""); await qc.invalidateQueries({ queryKey: ["task-comments", taskId] }); await refresh(); },
  });
  const upload = useMutation({
    mutationFn: (file: File) => tasksApi.upload(taskId!, file),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["task-attachments", taskId] }); },
  });

  const submitPrompt = () => {
    if (prompt === "block" || prompt === "cancel") act.mutate({ endpoint: prompt, body: { reason } });
    if (prompt === "verify") act.mutate({ endpoint: "verify", body: { remarks } });
    if (prompt === "reject_verification") act.mutate({ endpoint: "reject-verification", body: { reason, remarks } });
    if (prompt === "reassign") act.mutate({ endpoint: "reassign", body: { assigned_to: Number(newAssignee), note: remarks } });
    if (prompt === "complete") act.mutate({ endpoint: "complete", body: { work_response: workResponse.trim() } });
  };
  const promptReady =
    (prompt === "block" || prompt === "cancel") ? !!reason.trim()
      : prompt === "reject_verification" ? !!reason.trim() && !!remarks.trim()
        : prompt === "reassign" ? !!newAssignee
          : prompt === "complete" ? responseLength > 0 && responseLength <= WORK_RESPONSE_MAX
            : prompt === "verify";
  const workResponseErrors = prompt === "complete" && act.error instanceof ApiError ? act.error.fields?.work_response : undefined;

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) upload.mutate(file);
    event.target.value = "";
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md" aria-labelledby="task-detail-title">
      <DialogTitle id="task-detail-title">{task ? `${task.reference} · ${task.title}` : "Task"}</DialogTitle>
      <DialogContent dividers>
        {loadError && <Alert severity="error" role="alert">{conflictMessage(loadError)}</Alert>}
        {act.error && <Alert severity="warning" role="alert" sx={{ mb: 2 }}>{conflictMessage(act.error)}</Alert>}
        {task && (
          <Stack spacing={2}>
            <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
              <Chip label={STATUS_LABEL[task.status]} color={task.status === "BLOCKED" ? "warning" : "default"} />
              <Chip label={`Priority: ${PRIORITY_LABEL[task.priority]}`} variant="outlined" />
              <Chip label={`Verification: ${VERIFICATION_LABEL[task.verification_status]}`} variant="outlined" />
              {task.rework_count > 0 && <Chip label={`Rework ${task.rework_count}`} variant="outlined" />}
            </Stack>
            <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }} aria-label="Task actions">
              {SIMPLE_ACTIONS.filter((a) => allowed.has(a.action)).map((a) => (
                <Button key={a.action} variant="contained" size="small" disabled={act.isPending}
                  onClick={() => act.mutate({ endpoint: a.endpoint, body: {} })}>{a.label}</Button>
              ))}
              {allowed.has("complete") && (
                <Button variant="contained" size="small" disabled={act.isPending}
                  onClick={() => { act.reset(); setPrompt("complete"); }}>Submit Response &amp; Complete</Button>
              )}
              {PROMPT_ACTIONS.filter((a) => allowed.has(a.action)).map((a) => (
                <Button key={a.action} variant="outlined" size="small" disabled={act.isPending}
                  onClick={() => setPrompt(a.action)}>{a.label}</Button>
              ))}
            </Stack>
            {prompt && (
              <Box sx={{ p: 2, border: 1, borderColor: "divider", borderRadius: 1 }}>
                <Stack spacing={2}>
                  {prompt === "complete" && (
                    <TextField
                      label="Work performed" value={workResponse} onChange={(e) => setWorkResponse(e.target.value)}
                      required multiline minRows={3} disabled={act.isPending}
                      error={!!workResponseErrors || responseLength > WORK_RESPONSE_MAX}
                      helperText={workResponseErrors?.join(" ") ?? "Describe the work you did. The task is completed immediately; your manager can read this response."}
                    />
                  )}
                  {prompt === "reassign" && (
                    <TextField select label="New assignee" value={newAssignee} onChange={(e) => setNewAssignee(e.target.value)}>
                      {assignees.filter((a) => a.id !== task.assigned_to.id).map((a) =>
                        <MenuItem key={a.id} value={a.id}>{a.full_name} ({a.department.code})</MenuItem>)}
                    </TextField>
                  )}
                  {(prompt === "block" || prompt === "cancel" || prompt === "reject_verification") && (
                    <TextField label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} required />
                  )}
                  {(prompt === "verify" || prompt === "reject_verification" || prompt === "reassign") && (
                    <TextField label={prompt === "reassign" ? "Note" : "Remarks"} value={remarks}
                      onChange={(e) => setRemarks(e.target.value)} required={prompt === "reject_verification"} multiline />
                  )}
                  <Stack direction="row" spacing={1}>
                    <Button variant="contained" disabled={!promptReady || act.isPending} onClick={submitPrompt}>
                      {prompt === "complete" ? (act.isPending ? "Submitting…" : "Submit & complete") : "Confirm"}
                    </Button>
                    <Button disabled={act.isPending} onClick={() => { if (prompt === "complete") act.reset(); closePrompt(); }}>Back</Button>
                  </Stack>
                </Stack>
              </Box>
            )}
            <Stack spacing={0.75}>
              <Field label="Assigned to">{task.assigned_to.full_name} ({task.department.code})</Field>
              <Field label="Created by">{task.created_by.full_name || task.created_by.email}</Field>
              <Field label="Assigned by">{task.assigned_by.full_name || task.assigned_by.email} · <DateTimeText value={task.assigned_at} /></Field>
              <Field label="Received">{task.received_at ? <><DateTimeText value={task.received_at} /> via {task.received_at_source}</> : "Not recorded"}</Field>
              <Field label="Created"><DateTimeText value={task.created_at} /></Field>
              <Field label="Acknowledgment">{acknowledgmentLabel(task)}</Field>
              <Field label="Started"><DateTimeText value={task.started_at} /></Field>
              <Field label="Completed"><DateTimeText value={task.completed_at} /></Field>
              {task.status === "BLOCKED" && <Field label="On hold because">{task.blocked_reason}</Field>}
              {task.status === "CANCELLED" && <Field label="Cancelled because">{task.cancelled_reason}</Field>}
              <Field label="SLA">Deadlines and SLA tracking arrive in a later phase.</Field>
            </Stack>
            {task.description && <Typography sx={{ whiteSpace: "pre-wrap" }}>{task.description}</Typography>}
            {task.work_response && (
              <Box aria-label="Work response" component="section">
                <Typography variant="h3" component="h2">Work response</Typography>
                {task.status !== "COMPLETED" && (
                  <Typography variant="body2" color="text.secondary">Previous response — the task was reopened and needs a new response to complete.</Typography>
                )}
                <Typography variant="caption" color="text.secondary">
                  {task.work_response.author.full_name || task.work_response.author.email} · <DateTimeText value={task.work_response.created_at} />
                </Typography>
                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{task.work_response.body}</Typography>
              </Box>
            )}
            <Divider />
            <Typography variant="h3" component="h2">Verification history</Typography>
            {task.verifications.length === 0 ? <Typography variant="body2" color="text.secondary">No verification decisions yet.</Typography> : (
              <Stack spacing={1}>
                {task.verifications.map((v) => (
                  <Typography key={v.cycle_no} variant="body2">
                    Cycle {v.cycle_no}: <strong>{v.decision === "VERIFIED" ? "Verified" : "Rejected"}</strong> by {v.decided_by.full_name || v.decided_by.email}, <DateTimeText value={v.decided_at} />
                    {v.rejection_reason && <> — {v.rejection_reason}</>}{v.remarks && <> ({v.remarks})</>}
                  </Typography>
                ))}
              </Stack>
            )}
            <Typography variant="h3" component="h2">Assignment history</Typography>
            <Stack spacing={0.5}>
              {task.assignments.map((a) => (
                <Typography key={a.id} variant="body2">
                  {a.from_employee ? `${a.from_employee.full_name} → ` : ""}{a.to_employee.full_name} by {a.assigned_by.full_name || a.assigned_by.email}, <DateTimeText value={a.assigned_at} />{a.note && ` — ${a.note}`}
                </Typography>
              ))}
            </Stack>
            <Divider />
            <Typography variant="h3" component="h2">Attachments</Typography>
            {upload.error && <Alert severity="error" role="alert">{conflictMessage(upload.error)}{upload.error instanceof ApiError && upload.error.fields.file ? ` ${upload.error.fields.file.join(" ")}` : ""}</Alert>}
            {attachments.length === 0 ? <Typography variant="body2" color="text.secondary">No attachments.</Typography> : (
              <Stack spacing={0.5}>
                {attachments.map((a) => (
                  <Link key={a.id} href={tasksApi.downloadUrl(task.id, a.id)}>{a.original_filename}</Link>
                ))}
              </Stack>
            )}
            {allowed.has("attach") && (
              <Button component="label" variant="outlined" size="small" sx={{ alignSelf: "flex-start" }} disabled={upload.isPending}>
                Attach file (PDF, PNG, JPG, XLSX, EML · max 10 MB)
                <input hidden type="file" accept=".pdf,.png,.jpg,.jpeg,.xlsx,.eml" onChange={onFile} />
              </Button>
            )}
            <Divider />
            <Typography variant="h3" component="h2">Comments</Typography>
            {comments.length === 0 ? <Typography variant="body2" color="text.secondary">No comments yet.</Typography> : (
              <Stack spacing={1}>
                {comments.map((c) => (
                  <Box key={c.id}>
                    <Typography variant="caption" color="text.secondary">{c.author.full_name || c.author.email} · <DateTimeText value={c.created_at} /></Typography>
                    {c.kind === "WORK_RESPONSE" && <Chip size="small" variant="outlined" color="primary" label="Work response" sx={{ ml: 1, height: 18 }} />}
                    <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{c.body}</Typography>
                  </Box>
                ))}
              </Stack>
            )}
            {allowed.has("comment") && (
              <Stack direction="row" spacing={1}>
                <TextField label="Add a comment" value={comment} onChange={(e) => setComment(e.target.value)} multiline />
                <Button variant="outlined" disabled={!comment.trim() || addComment.isPending} onClick={() => addComment.mutate()}>Post</Button>
              </Stack>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
}

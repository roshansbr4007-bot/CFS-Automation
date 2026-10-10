/** Phase 7.5A: lifecycle pieces shared by plans, benchmark rules and band schemes.
 *
 * The rule (identical for all three): only a DRAFT is edited (HR). ACTIVE and RETIRED versions
 * are read-only for everyone; the only state changes are Admin's lifecycle actions - Activate a
 * DRAFT, Retire an ACTIVE version with a last day and a reason. Retiring closes a version, it never
 * changes its contents. The backend enforces all of it; these controls only reflect it. */
import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, Stack, TextField,
  Typography,
} from "@mui/material";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "../../api/apiClient";
import { cfgErrors } from "./api";
import { MODEL_LABEL, STATUS_COLOR, STATUS_LABEL } from "./labels";

export function StatusChip({ status }: { status: string }) {
  return <Chip size="small" label={STATUS_LABEL[status] ?? status} color={STATUS_COLOR[status] ?? "default"} />;
}

export function ModelChip({ model }: { model: string }) {
  return <Chip size="small" variant="outlined" label={MODEL_LABEL[model] ?? model} />;
}

/** Shown on every non-draft version: why nothing can be edited and what to do instead. */
export function LockedNotice({ status, legacy = false, canClone = false }: {
  status: string; legacy?: boolean; canClone?: boolean;
}) {
  if (legacy) {
    return <Alert severity="info">Legacy (0–100) version: read-only here. Only Admin can retire it while it is active.</Alert>;
  }
  if (status === "DRAFT") return null;
  return (
    <Alert severity="info">
      This version is {STATUS_LABEL[status]?.toLowerCase() ?? status} and cannot be edited by anyone.
      {canClone ? " To change it, clone it into a new draft." : " HR can clone it into a new draft."}
      {status === "ACTIVE" ? " Admin can retire it." : ""}
    </Alert>
  );
}

/** The server's message plus every field problem it returned (e.g. the activation problem list). */
export function ProblemList({ error }: { error: unknown }) {
  if (!error) return null;
  if (!(error instanceof ApiError)) {
    return <Alert severity="error" role="alert">Something went wrong. Try again.</Alert>;
  }
  // One item per message; the activation problem list is shown as it is, other fields are named.
  const items = Object.entries(cfgErrors(error)).flatMap(([field, message]) => {
    const list = (error.fields ?? {})[field];
    const messages = Array.isArray(list) && list.every((m) => typeof m === "string") ? list : [message];
    return messages.map((m) => (field === "activation" || field === "non_field_errors" ? m : `${field}: ${m}`));
  });
  return (
    <Alert severity="error" role="alert">
      {error.message}
      {items.length > 0 && <ul style={{ margin: "4px 0 0", paddingLeft: 20 }}>{items.map((m) => <li key={m}>{m}</li>)}</ul>}
    </Alert>
  );
}

/** The part of a configuration mutation these dialogs use (any useCfgMutation result fits). */
export interface Runnable<TArgs = void> {
  mutate: (args: TArgs) => void;
  reset: () => void;
  isPending: boolean;
  error: Error | null;
}

/** A yes / no action (activate, clone, delete) backed by a configuration mutation. */
export function ConfirmDialog({
  open, title, confirmLabel, color = "primary", mutation, onClose, children,
}: {
  open: boolean; title: string; confirmLabel: string; color?: "primary" | "error" | "warning" | "success";
  mutation: Runnable; onClose: () => void; children: ReactNode;
}) {
  useEffect(() => { if (open) mutation.reset(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="confirm-title">
      <DialogTitle id="confirm-title">{title}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          <ProblemList error={mutation.error} />
          <DialogContentText component="div">{children}</DialogContentText>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color={color} disabled={mutation.isPending} onClick={() => mutation.mutate()}>
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/** Admin: retire an ACTIVE version. A last day and a reason are both required (backend rule). */
export function RetireDialog({
  open, title, mutation, onClose,
}: {
  open: boolean; title: string;
  mutation: Runnable<{ last_day: string; reason: string }>;
  onClose: () => void;
}) {
  const [lastDay, setLastDay] = useState("");
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    if (open) { setLastDay(""); setReason(""); setChecked(false); mutation.reset(); }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const fields = mutation.error instanceof ApiError ? mutation.error.fields : {};
  const missingDay = checked && !lastDay;
  const missingReason = checked && !reason.trim();
  const save = () => {
    setChecked(true);
    if (!lastDay || !reason.trim()) return;
    mutation.mutate({ last_day: lastDay, reason: reason.trim() });
  };
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="retire-title">
      <DialogTitle id="retire-title">{title}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {mutation.error && !fields.last_day && !fields.reason && <ProblemList error={mutation.error} />}
          <DialogContentText>
            Retiring closes this version on its last day. Its contents never change, and it cannot be activated again.
          </DialogContentText>
          <TextField size="small" type="date" label="Last day" value={lastDay} onChange={(e) => setLastDay(e.target.value)}
            InputLabelProps={{ shrink: true }} error={missingDay || !!fields.last_day}
            helperText={missingDay ? "Choose the last day." : fields.last_day?.join(" ")} />
          <TextField size="small" label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} multiline minRows={2}
            error={missingReason || !!fields.reason} helperText={missingReason ? "A reason is required." : fields.reason?.join(" ")} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="warning" disabled={mutation.isPending} onClick={save}>Retire</Button>
      </DialogActions>
    </Dialog>
  );
}

/** A label / value row for read-only details. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Stack direction="row" spacing={2}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 200 }}>{label}</Typography>
      <Typography variant="body2" component="div">{children}</Typography>
    </Stack>
  );
}

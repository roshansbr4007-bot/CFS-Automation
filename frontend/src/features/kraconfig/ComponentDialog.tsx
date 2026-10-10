/** Phase 7.5A: add or edit a component of a KPI line in a DRAFT plan (HR). A component counts the
 * tasks of one responsibility, or is a manual entry HR scores. The backend validates every
 * combination; a draft may leave scope / matching / verification open (activation needs them). */
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "../../api/apiClient";
import { responsibilitiesApi } from "../../api/endpoints";
import type { KpiCfgComponent, KpiCfgLine } from "../../api/types";
import { cfgApi, cfgErrors, changed, useCfgMutation } from "./api";
import { MANUAL_MATCHES, SOURCE_TYPES, TASK_SCOPES, VERIFICATION_POLICIES } from "./labels";

const MANUAL = "MANUAL_ENTRY";

export function ComponentDialog({
  open, line, component, onClose,
}: { open: boolean; line: KpiCfgLine | null; component: KpiCfgComponent | "new" | null; onClose: () => void }) {
  const existing = component && component !== "new" ? component : null;
  const { data: responsibilities = [] } = useQuery({
    queryKey: ["responsibilities"], queryFn: responsibilitiesApi.list, enabled: open,
  });
  const [source, setSource] = useState("RESPONSIBILITY_TASKS");
  const [responsibility, setResponsibility] = useState("");
  const [label, setLabel] = useState("");
  const [share, setShare] = useState("1");
  const [scope, setScope] = useState("");
  const [match, setMatch] = useState("");
  const [policy, setPolicy] = useState("");
  const [position, setPosition] = useState("");
  const manual = source === MANUAL;
  // A manual entry has no responsibility, scope, matching or verification (backend rule). Numbers
  // are sent as typed; when editing, only what changed is sent.
  const values = {
    source_type: source,
    responsibility: manual || !responsibility ? null : Number(responsibility),
    label,
    contribution_share: share,
    task_scope: manual ? "" : scope,
    manual_match: manual || scope === "SCHEDULED" ? "" : match,
    verification_policy: manual ? "" : policy,
    ...(position.trim() ? { position: position.trim() } : {}),
  };
  const body = existing
    ? changed({
      source_type: existing.source_type, responsibility: existing.responsibility?.id ?? null, label: existing.label,
      contribution_share: existing.contribution_share, task_scope: existing.task_scope, manual_match: existing.manual_match,
      verification_policy: existing.verification_policy, position: String(existing.position),
    }, values)
    : values;
  const save = useCfgMutation(
    () => (existing ? cfgApi.updateComponent(existing.id, body) : cfgApi.createComponent((line as KpiCfgLine).id, body)),
    () => onClose(),
  );
  useEffect(() => {
    if (!open) return;
    setSource(existing?.source_type ?? "RESPONSIBILITY_TASKS");
    setResponsibility(existing?.responsibility ? String(existing.responsibility.id) : "");
    setLabel(existing?.label ?? "");
    setShare(existing?.contribution_share ?? "1");
    setScope(existing?.task_scope ?? "");
    setMatch(existing?.manual_match ?? "");
    setPolicy(existing?.verification_policy ?? "");
    setPosition(existing ? String(existing.position) : "");
    save.reset();
  }, [open, existing?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(save.error);
  const missing = (manual ? !label.trim() : !responsibility) || (!!existing && Object.keys(body).length === 0);
  const active = responsibilities.filter((r) => r.is_active || String(r.id) === responsibility);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="component-title">
      <DialogTitle id="component-title">{existing ? "Edit component" : `Add component to ${line?.name ?? ""}`}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {save.error instanceof ApiError && Object.keys(errors).length === 0 && <Alert severity="error">{save.error.message}</Alert>}
          <TextField size="small" select label="Source" value={source} onChange={(e) => setSource(String(e.target.value))}
            error={!!errors.source_type} helperText={errors.source_type}>
            {SOURCE_TYPES.map(([value, text]) => <MenuItem key={value} value={value}>{text}</MenuItem>)}
          </TextField>
          <TextField size="small" label={manual ? "Name of the manual entry" : "Label (optional)"} value={label}
            onChange={(e) => setLabel(e.target.value)} error={!!errors.label}
            helperText={errors.label ?? (manual ? "HR enters this score during the monthly review." : "Shown instead of the responsibility's name.")} />
          {!manual && (
            <>
              <TextField size="small" select label="Responsibility" value={responsibility}
                onChange={(e) => setResponsibility(String(e.target.value))} error={!!errors.responsibility} helperText={errors.responsibility}>
                {active.map((r) => <MenuItem key={r.id} value={String(r.id)}>{r.name} ({r.department.code})</MenuItem>)}
              </TextField>
              <TextField size="small" select label="Task scope" value={scope} onChange={(e) => setScope(String(e.target.value))}
                error={!!errors.task_scope} helperText={errors.task_scope ?? "Required before activation."}>
                <MenuItem value="">Choose later</MenuItem>
                {TASK_SCOPES.map(([value, text]) => <MenuItem key={value} value={value}>{text}</MenuItem>)}
              </TextField>
              <TextField size="small" select label="Manual task matching" value={scope === "SCHEDULED" ? "" : match}
                disabled={scope === "SCHEDULED"} onChange={(e) => setMatch(String(e.target.value))} error={!!errors.manual_match}
                helperText={errors.manual_match ?? (scope === "SCHEDULED" ? "Scheduled-only components do not match manual tasks." : "Required before activation when manual tasks count.")}>
                <MenuItem value="">Choose later</MenuItem>
                {MANUAL_MATCHES.map(([value, text]) => <MenuItem key={value} value={value}>{text}</MenuItem>)}
              </TextField>
              <TextField size="small" select label="Verification policy" value={policy} onChange={(e) => setPolicy(String(e.target.value))}
                error={!!errors.verification_policy} helperText={errors.verification_policy ?? "Required before activation."}>
                <MenuItem value="">Choose later</MenuItem>
                {VERIFICATION_POLICIES.map(([value, text]) => <MenuItem key={value} value={value}>{text}</MenuItem>)}
              </TextField>
            </>
          )}
          <TextField size="small" label="Contribution share" value={share} onChange={(e) => setShare(e.target.value)}
            inputProps={{ inputMode: "decimal" }} error={!!errors.contribution_share}
            helperText={errors.contribution_share ?? "Relative weight within the line (1 = equal)."} />
          <TextField size="small" label="Position (optional)" value={position} onChange={(e) => setPosition(e.target.value)}
            inputProps={{ inputMode: "numeric" }} error={!!errors.position} helperText={errors.position} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={missing || save.isPending} onClick={() => save.mutate()}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

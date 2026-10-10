/** Phase 7.5A: one benchmark (scoring) rule version - achievement % steps to score %. HR edits a
 * DRAFT (steps are replaced as a whole) and clones any version; Admin activates a DRAFT and
 * retires an ACTIVE version. ACTIVE and RETIRED versions are read-only for everyone. */
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, Link, Paper, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../../api/apiClient";
import { PERM, type KpiCfgScoringRule, type KpiCfgStep } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DateTimeText } from "../../components/DateTimeText";
import { cfgApi, cfgErrors, changed, useCfgMutation, useScoringRule } from "./api";
import { ConfirmDialog, Fact, LockedNotice, RetireDialog, StatusChip } from "./LifecycleDialogs";
import { period, plain } from "./labels";

/** Editable rows of benchmark steps. Values are sent as typed; the backend validates them. */
export function StepsEditor({
  steps, onChange, errors,
}: { steps: KpiCfgStep[]; onChange: (steps: KpiCfgStep[]) => void; errors: Record<string, string> }) {
  const set = (index: number, key: keyof KpiCfgStep, value: string) =>
    onChange(steps.map((s, i) => (i === index ? { ...s, [key]: value } : s)));
  return (
    <Stack spacing={1} role="group" aria-label="Benchmark steps">
      {steps.map((step, index) => (
        <Stack key={index} direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
          <TextField size="small" label={`Step ${index + 1}: achievement at least %`} value={step.min_achievement_pct}
            onChange={(e) => set(index, "min_achievement_pct", e.target.value)} sx={{ flex: 1 }} inputProps={{ inputMode: "decimal" }}
            error={!!errors[`steps.${index}`] || !!errors[`steps.${index}.min_achievement_pct`]}
            helperText={errors[`steps.${index}`] ?? errors[`steps.${index}.min_achievement_pct`]} />
          <TextField size="small" label={`Step ${index + 1}: score %`} value={step.score_pct}
            onChange={(e) => set(index, "score_pct", e.target.value)} sx={{ flex: 1 }} inputProps={{ inputMode: "decimal" }}
            error={!!errors[`steps.${index}.score_pct`]} helperText={errors[`steps.${index}.score_pct`]} />
          <IconButton aria-label={`Remove step ${index + 1}`} onClick={() => onChange(steps.filter((_, i) => i !== index))}>×</IconButton>
        </Stack>
      ))}
      {errors.steps && <Typography variant="caption" color="error">{errors.steps}</Typography>}
      <div><Button size="small" onClick={() => onChange([...steps, { min_achievement_pct: "", score_pct: "" }])}>Add step</Button></div>
    </Stack>
  );
}

export function ScoringRuleEditorPage() {
  const id = Number(useParams().id);
  const valid = Number.isInteger(id) && id > 0;
  const { data: rule, error } = useScoringRule(id);
  const back = <Link component={RouterLink} to="/performance/config?tab=scoring-rules">← KRA configuration</Link>;
  if (!valid || (error instanceof ApiError && error.status === 404)) {
    return <Stack spacing={2}>{back}<Alert severity="error">This benchmark rule does not exist.</Alert></Stack>;
  }
  if (error) return <Stack spacing={2}>{back}<ApiErrorAlert error={error} /></Stack>;
  if (!rule) return <Typography color="text.secondary">Loading…</Typography>;
  return <Stack spacing={3}>{back}<ScoringRuleEditor rule={rule} /></Stack>;
}

function ScoringRuleEditor({ rule }: { rule: KpiCfgScoringRule }) {
  const { hasPerm } = useAuth();
  const navigate = useNavigate();
  const draft = rule.status === "DRAFT";
  const canEdit = hasPerm(PERM.configureKpis) && draft;
  const canClone = hasPerm(PERM.configureKpis);
  const canActivate = hasPerm(PERM.approveKpiConfig) && draft;
  const canRetire = hasPerm(PERM.approveKpiConfig) && rule.status === "ACTIVE";
  const [pending, setPending] = useState<"edit" | "activate" | "retire" | "clone" | null>(null);
  const close = () => setPending(null);
  const activate = useCfgMutation(() => cfgApi.activateScoringRule(rule.id), close);
  const retire = useCfgMutation((body: { last_day: string; reason: string }) => cfgApi.retireScoringRule(rule.id, body), close);
  const clone = useCfgMutation(() => cfgApi.cloneScoringRule(rule.id),
    (copy: KpiCfgScoringRule) => { close(); navigate(`/performance/config/scoring-rules/${copy.id}`); });
  const title = `${rule.name} (${rule.code} v${rule.version})`;
  return (
    <>
      <Stack direction="row" spacing={2} sx={{ justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap" }}>
        <Stack spacing={1}>
          <Typography variant="h2" component="h1">{rule.name}</Typography>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <Typography color="text.secondary">Benchmark rule {rule.code} v{rule.version}</Typography>
            <StatusChip status={rule.status} />
          </Stack>
        </Stack>
        <Stack direction="row" spacing={1}>
          {canClone && <Button onClick={() => setPending("clone")}>Clone as new draft</Button>}
          {canEdit && <Button variant="outlined" onClick={() => setPending("edit")}>Edit draft</Button>}
          {canActivate && <Button variant="contained" color="success" onClick={() => setPending("activate")}>Activate</Button>}
          {canRetire && <Button variant="contained" color="warning" onClick={() => setPending("retire")}>Retire</Button>}
        </Stack>
      </Stack>
      <LockedNotice status={rule.status} canClone={canClone} />
      <Paper component="section" aria-label="Benchmark rule details" sx={{ p: 2 }}>
        <Stack spacing={1}>
          <Fact label="Effective">{period(rule.effective_from, rule.effective_to)}</Fact>
          <Fact label="Score below the lowest step">{rule.below_min_score_pct === null ? "—" : `${plain(rule.below_min_score_pct)} %`}</Fact>
          {rule.activated_at && <Fact label="Activated"><DateTimeText value={rule.activated_at} /></Fact>}
          {rule.retired_at && <Fact label="Retired"><DateTimeText value={rule.retired_at} /> — {rule.retire_reason}</Fact>}
        </Stack>
      </Paper>
      <Paper>
        <Table size="small" aria-label="Benchmark steps table">
          <TableHead><TableRow><TableCell>Achievement at least</TableCell><TableCell>Score</TableCell></TableRow></TableHead>
          <TableBody>
            {rule.steps.length === 0 && <TableRow><TableCell colSpan={2}>No steps yet.</TableCell></TableRow>}
            {rule.steps.map((s) => (
              <TableRow key={`${s.min_achievement_pct}-${s.score_pct}`}>
                <TableCell>{plain(s.min_achievement_pct)} %</TableCell><TableCell>{plain(s.score_pct)} %</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>
      <EditRuleDialog rule={rule} open={pending === "edit"} onClose={close} />
      <ConfirmDialog open={pending === "activate"} title={`Activate ${title}?`} confirmLabel="Activate" color="success"
        mutation={activate} onClose={close}>
        Once active, this benchmark rule can never be edited again. The server checks it before activating.
      </ConfirmDialog>
      <ConfirmDialog open={pending === "clone"} title={`Clone ${title}?`} confirmLabel="Clone" mutation={clone} onClose={close}>
        A new draft is created with the next version number and the same steps. This version does not change.
      </ConfirmDialog>
      <RetireDialog open={pending === "retire"} title={`Retire ${title}`} mutation={retire} onClose={close} />
    </>
  );
}

function EditRuleDialog({ rule, open, onClose }: { rule: KpiCfgScoringRule; open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [below, setBelow] = useState("");
  const [steps, setSteps] = useState<KpiCfgStep[]>([]);
  const stored = {
    name: rule.name, effective_from: rule.effective_from, effective_to: rule.effective_to,
    below_min_score_pct: rule.below_min_score_pct,
    steps: rule.steps.map((s) => ({ min_achievement_pct: s.min_achievement_pct, score_pct: s.score_pct })),
  };
  // Only what changed is sent; the steps (replaced as a whole on the server) only when a step changed.
  const body = changed(stored, {
    name, effective_from: from, effective_to: to || null, below_min_score_pct: below.trim() ? below.trim() : null, steps,
  });
  const save = useCfgMutation(() => cfgApi.updateScoringRule(rule.id, body), () => onClose());
  useEffect(() => {
    if (!open) return;
    setName(stored.name); setFrom(stored.effective_from); setTo(stored.effective_to ?? "");
    setBelow(stored.below_min_score_pct ?? ""); setSteps(stored.steps.map((s) => ({ ...s }))); save.reset();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(save.error);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="rule-edit-title">
      <DialogTitle id="rule-edit-title">Edit draft benchmark rule</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {save.error instanceof ApiError && Object.keys(errors).length === 0 && <Alert severity="error">{save.error.message}</Alert>}
          <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} error={!!errors.name} helperText={errors.name} />
          <Stack direction="row" spacing={2}>
            <TextField size="small" type="date" label="Effective from" value={from} onChange={(e) => setFrom(e.target.value)} sx={{ flex: 1 }}
              InputLabelProps={{ shrink: true }} error={!!errors.effective_from} helperText={errors.effective_from} />
            <TextField size="small" type="date" label="Effective to (optional)" value={to} onChange={(e) => setTo(e.target.value)} sx={{ flex: 1 }}
              InputLabelProps={{ shrink: true }} error={!!errors.effective_to} helperText={errors.effective_to} />
          </Stack>
          <TextField size="small" label="Score below the lowest step % (optional)" value={below} onChange={(e) => setBelow(e.target.value)}
            inputProps={{ inputMode: "decimal" }} error={!!errors.below_min_score_pct} helperText={errors.below_min_score_pct} />
          <StepsEditor steps={steps} onChange={setSteps} errors={errors} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!name.trim() || !from || Object.keys(body).length === 0 || save.isPending}
          onClick={() => save.mutate()}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

/** Phase 7.5A: benchmark (scoring) rule versions. HR creates and clones drafts; Admin activates
 * and retires on the rule's own page. */
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField, Typography,
} from "@mui/material";
import { useEffect, useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";

import { ApiError } from "../../api/apiClient";
import { PERM, type KpiCfgScoringRule, type KpiCfgStep } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { cfgApi, cfgErrors, useCfgMutation, useScoringRules } from "./api";
import { StatusChip } from "./LifecycleDialogs";
import { period } from "./labels";
import { StepsEditor } from "./ScoringRuleEditorPage";

const PAGE = 25;

export function ScoringRulesTab() {
  const { hasPerm } = useAuth();
  const canPrepare = hasPerm(PERM.configureKpis);
  const { data = [], isFetching, error } = useScoringRules();
  const [creating, setCreating] = useState(false);
  const [page, setPage] = useState(0); // the whole list is loaded; the table pages through it
  const columns: Column<KpiCfgScoringRule>[] = [
    { key: "rule", header: "Benchmark rule", render: (r) => <Stack><strong>{r.name}</strong><Typography variant="caption" color="text.secondary">{r.code} v{r.version}</Typography></Stack> },
    { key: "status", header: "Status", render: (r) => <StatusChip status={r.status} /> },
    { key: "period", header: "Effective", render: (r) => period(r.effective_from, r.effective_to) },
    { key: "steps", header: "Steps", render: (r) => r.steps.length },
    { key: "open", header: "", render: (r) => (
      <Button size="small" component={RouterLink} to={`/performance/config/scoring-rules/${r.id}`}
        aria-label={`Open ${r.code} v${r.version}`}>Open</Button>
    ) },
  ];
  const shown = Math.min(page, Math.max(0, Math.ceil(data.length / PAGE) - 1)); // a list that shrank
  return (
    <Stack spacing={2}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Typography color="text.secondary">How a KPI's achievement % becomes its score %. A plan line uses one benchmark rule.</Typography>
        {canPrepare && <Button variant="contained" onClick={() => setCreating(true)}>New draft benchmark rule</Button>}
      </Stack>
      <ApiErrorAlert error={error} />
      <DataTable caption="Benchmark rules" columns={columns} rows={data.slice(shown * PAGE, (shown + 1) * PAGE)}
        getRowId={(r) => r.id} loading={isFetching} total={data.length} page={shown} onPageChange={setPage} emptyMessage="No benchmark rules yet." />
      <CreateRuleDialog open={creating} onClose={() => setCreating(false)} />
    </Stack>
  );
}

function CreateRuleDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [below, setBelow] = useState("");
  const [steps, setSteps] = useState<KpiCfgStep[]>([]);
  const create = useCfgMutation(() => cfgApi.createScoringRule({
    code, name, effective_from: from, effective_to: to || null, below_min_score_pct: below.trim() ? below : null, steps,
  }), (rule: KpiCfgScoringRule) => { onClose(); navigate(`/performance/config/scoring-rules/${rule.id}`); });
  useEffect(() => {
    if (open) { setCode(""); setName(""); setFrom(""); setTo(""); setBelow(""); setSteps([]); create.reset(); }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(create.error);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="new-rule-title">
      <DialogTitle id="new-rule-title">New draft benchmark rule</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {create.error instanceof ApiError && Object.keys(errors).length === 0 && <Alert severity="error">{create.error.message}</Alert>}
          <TextField size="small" label="Code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())}
            error={!!errors.code} helperText={errors.code ?? "An existing code gets its next version number."} />
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
        <Button variant="contained" disabled={!code.trim() || !name.trim() || !from || create.isPending} onClick={() => create.mutate()}>Create draft</Button>
      </DialogActions>
    </Dialog>
  );
}

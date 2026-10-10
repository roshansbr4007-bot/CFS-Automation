/** Phase 7.5A: KPI plan versions. HR creates drafts and clones any KRA version into a new draft;
 * legacy (0-100) versions are listed read-only. Opening a version shows its editor / details. */
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField, Typography,
} from "@mui/material";
import { useEffect, useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";

import { ApiError } from "../../api/apiClient";
import { PERM, type KpiCfgPlan, type KpiCfgPlanDetail } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { cfgApi, cfgErrors, useBandSchemes, useCfgMutation, usePlans } from "./api";
import { ConfirmDialog, ModelChip, StatusChip } from "./LifecycleDialogs";
import { period, planTitle } from "./labels";

const PAGE = 25;

export function PlansTab() {
  const { hasPerm } = useAuth();
  const canPrepare = hasPerm(PERM.configureKpis);
  const navigate = useNavigate();
  const { data = [], isFetching, error } = usePlans();
  const [creating, setCreating] = useState(false);
  const [cloning, setCloning] = useState<KpiCfgPlan | null>(null);
  const [cloneOpen, setCloneOpen] = useState(false); // the row stays set while its dialog closes
  const clone = useCfgMutation(() => cfgApi.clonePlan((cloning as KpiCfgPlan).id),
    (copy: KpiCfgPlanDetail) => navigate(`/performance/config/plans/${copy.id}`));

  const [page, setPage] = useState(0); // the whole list is loaded; the table pages through it
  const columns: Column<KpiCfgPlan>[] = [
    { key: "plan", header: "Plan", render: (p) => <Stack><strong>{p.name}</strong><Typography variant="caption" color="text.secondary">{p.configuration} v{p.version}</Typography></Stack> },
    { key: "model", header: "Model", render: (p) => <ModelChip model={p.calculation_model} /> },
    { key: "status", header: "Status", render: (p) => <StatusChip status={p.status} /> },
    { key: "period", header: "Effective", render: (p) => period(p.effective_from, p.effective_to) },
    { key: "bands", header: "Band scheme", render: (p) => p.band_scheme ? `${p.band_scheme.code} v${p.band_scheme.version}` : "—" },
    { key: "actions", header: "", render: (p) => (
      <Stack direction="row" spacing={1}>
        <Button size="small" component={RouterLink} to={`/performance/config/plans/${p.id}`}
          aria-label={`Open ${planTitle(p)}`}>Open</Button>
        {canPrepare && p.calculation_model === "KRA_POINTS" && (
          <Button size="small" onClick={() => { setCloning(p); setCloneOpen(true); }} aria-label={`Clone ${planTitle(p)}`}>Clone</Button>
        )}
      </Stack>
    ) },
  ];

  const shown = Math.min(page, Math.max(0, Math.ceil(data.length / PAGE) - 1)); // a list that shrank
  return (
    <Stack spacing={2}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Typography color="text.secondary">
          A plan version scores one month on the KRA 10-point scale. Only a draft can be edited.
        </Typography>
        {canPrepare && <Button variant="contained" onClick={() => setCreating(true)}>New draft plan</Button>}
      </Stack>
      <ApiErrorAlert error={error} />
      <DataTable caption="KRA plan versions" columns={columns} rows={data.slice(shown * PAGE, (shown + 1) * PAGE)}
        getRowId={(p) => p.id} loading={isFetching} total={data.length} page={shown} onPageChange={setPage} emptyMessage="No plan versions yet." />
      <CreatePlanDialog open={creating} onClose={() => setCreating(false)} />
      <ConfirmDialog open={cloneOpen} title={`Clone ${cloning ? planTitle(cloning) : ""}?`} confirmLabel="Clone"
        mutation={clone} onClose={() => setCloneOpen(false)}>
        A new draft is created with the next version number, copying the KPI lines, components and deduction rules.
        The original version does not change.
      </ConfirmDialog>
    </Stack>
  );
}

function CreatePlanDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const { data: schemes = [] } = useBandSchemes(open);
  const [configuration, setConfiguration] = useState("");
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [scheme, setScheme] = useState("");
  const create = useCfgMutation(
    () => cfgApi.createPlan({
      configuration, name, effective_from: from, effective_to: to || null, band_scheme: scheme ? Number(scheme) : null,
    }),
    (plan: KpiCfgPlanDetail) => { onClose(); navigate(`/performance/config/plans/${plan.id}`); },
  );
  useEffect(() => {
    if (open) { setConfiguration(""); setName(""); setFrom(""); setTo(""); setScheme(""); create.reset(); }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(create.error);
  const missing = !configuration.trim() || !name.trim() || !from;
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="new-plan-title">
      <DialogTitle id="new-plan-title">New draft plan</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {create.error instanceof ApiError && Object.keys(errors).length === 0 && <Alert severity="error">{create.error.message}</Alert>}
          <TextField size="small" label="Plan family code" value={configuration} onChange={(e) => setConfiguration(e.target.value.toUpperCase())}
            error={!!errors.configuration} helperText={errors.configuration ?? "For example OPERATIONS_KRA. The next version number is given automatically."} />
          <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} error={!!errors.name} helperText={errors.name} />
          <Stack direction="row" spacing={2}>
            <TextField size="small" type="date" label="Effective from" value={from} onChange={(e) => setFrom(e.target.value)}
              InputLabelProps={{ shrink: true }} sx={{ flex: 1 }} error={!!errors.effective_from} helperText={errors.effective_from} />
            <TextField size="small" type="date" label="Effective to (optional)" value={to} onChange={(e) => setTo(e.target.value)}
              InputLabelProps={{ shrink: true }} sx={{ flex: 1 }} error={!!errors.effective_to} helperText={errors.effective_to} />
          </Stack>
          <TextField size="small" select label="Band scheme (optional)" value={scheme} onChange={(e) => setScheme(String(e.target.value))}
            error={!!errors.band_scheme} helperText={errors.band_scheme}>
            <MenuItem value="">Choose later</MenuItem>
            {schemes.filter((s) => s.status !== "RETIRED").map((s) => (
              <MenuItem key={s.id} value={String(s.id)}>{s.name} ({s.code} v{s.version}, {s.status.toLowerCase()})</MenuItem>
            ))}
          </TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={missing || create.isPending} onClick={() => create.mutate()}>Create draft</Button>
      </DialogActions>
    </Dialog>
  );
}

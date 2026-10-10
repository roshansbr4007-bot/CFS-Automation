/** Phase 7.5A: add or edit a KPI line of a DRAFT plan (HR). The KPI of an existing line never
 * changes. Points totals and every other rule are checked by the backend (see readiness). */
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField,
} from "@mui/material";
import { useEffect, useState } from "react";

import { ApiError } from "../../api/apiClient";
import type { KpiCfgLine, KpiCfgPlanDetail } from "../../api/types";
import { cfgApi, cfgErrors, changed, useCfgMutation, useKpis, useScoringRules } from "./api";

export function PlanLineDialog({
  open, plan, line, onClose,
}: { open: boolean; plan: KpiCfgPlanDetail; line: KpiCfgLine | "new" | null; onClose: () => void }) {
  const existing = line && line !== "new" ? line : null;
  const { data: kpis = [] } = useKpis(open);
  const { data: rules = [] } = useScoringRules(open);
  const [kpi, setKpi] = useState("");
  const [weight, setWeight] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [rule, setRule] = useState("");
  const [position, setPosition] = useState("");
  // Numbers are sent as typed (the backend validates them); an empty position is left out. When
  // editing, only what changed is sent, so an untouched (even meanwhile retired) rule is never resent.
  const values = {
    weight, display_name: displayName, scoring_rule: rule ? Number(rule) : null,
    ...(position.trim() ? { position: position.trim() } : {}),
  };
  const body = existing
    ? changed({
      weight: existing.weight, display_name: existing.display_name, scoring_rule: existing.scoring_rule?.id ?? null,
      position: String(existing.position),
    }, values)
    : { ...values, kpi: Number(kpi) };
  const save = useCfgMutation(
    () => (existing ? cfgApi.updateLine(existing.id, body) : cfgApi.createLine(plan.id, body)), () => onClose(),
  );
  useEffect(() => {
    if (!open) return;
    setKpi(existing ? String(existing.kpi.id) : "");
    setWeight(existing?.weight ?? "");
    setDisplayName(existing?.display_name ?? "");
    setRule(existing?.scoring_rule ? String(existing.scoring_rule.id) : "");
    setPosition(existing ? String(existing.position) : "");
    save.reset();
  }, [open, existing?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(save.error);
  const used = new Set(plan.lines.map((l) => l.kpi.id));
  const choosable = kpis.filter((k) => k.is_active && !used.has(k.id));
  const missing = (!existing && !kpi) || !weight.trim() || (!!existing && Object.keys(body).length === 0);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="line-title">
      <DialogTitle id="line-title">{existing ? `Edit KPI line ${existing.name}` : "Add KPI line"}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {save.error instanceof ApiError && Object.keys(errors).length === 0 && <Alert severity="error">{save.error.message}</Alert>}
          {existing ? (
            <TextField size="small" label="KPI" value={existing.kpi.name} disabled helperText="The KPI of a line never changes." />
          ) : (
            <TextField size="small" select label="KPI" value={kpi} onChange={(e) => setKpi(String(e.target.value))}
              error={!!errors.kpi} helperText={errors.kpi ?? "Each KPI appears at most once in a plan."}>
              {choosable.map((k) => <MenuItem key={k.id} value={String(k.id)}>{k.name} ({k.code})</MenuItem>)}
            </TextField>
          )}
          <TextField size="small" label="Points (maximum for this KPI)" value={weight} onChange={(e) => setWeight(e.target.value)}
            inputProps={{ inputMode: "decimal" }} error={!!errors.weight}
            helperText={errors.weight ?? "All lines together must total exactly 10.00 before activation."} />
          <TextField size="small" label="Display name (optional)" value={displayName} onChange={(e) => setDisplayName(e.target.value)}
            error={!!errors.display_name} helperText={errors.display_name ?? "Shown instead of the KPI's name."} />
          <TextField size="small" select label="Benchmark rule" value={rule} onChange={(e) => setRule(String(e.target.value))}
            error={!!errors.scoring_rule} helperText={errors.scoring_rule}>
            <MenuItem value="">Choose later</MenuItem>
            {rules.filter((r) => r.status !== "RETIRED" || String(r.id) === rule).map((r) => (
              <MenuItem key={r.id} value={String(r.id)}>{r.name} ({r.code} v{r.version}, {r.status.toLowerCase()})</MenuItem>
            ))}
          </TextField>
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

/** Phase 7.5A: add or edit a deduction rule of a DRAFT plan (HR). The rule only records what HR
 * may apply later in the monthly review (range, scope, stacking, cap, priority, ceiling band); no
 * penalty is calculated here. The code of an existing rule never changes. */
import {
  Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Stack, TextField,
} from "@mui/material";
import { useEffect, useState } from "react";

import { ApiError } from "../../api/apiClient";
import type { KpiCfgDeductionRule, KpiCfgPlanDetail } from "../../api/types";
import { cfgApi, cfgErrors, changed, useBandScheme, useCfgMutation } from "./api";
import { DEDUCTION_KINDS, DEDUCTION_SCOPES, DEDUCTION_STACKING } from "./labels";

const CEILING = "BAND_CEILING";

export function DeductionRuleDialog({
  open, plan, rule, onClose,
}: { open: boolean; plan: KpiCfgPlanDetail; rule: KpiCfgDeductionRule | "new" | null; onClose: () => void }) {
  const existing = rule && rule !== "new" ? rule : null;
  const { data: scheme } = useBandScheme(open ? plan.band_scheme?.id : null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState("PERCENT_RANGE");
  const [scope, setScope] = useState("");
  const [minPct, setMinPct] = useState("");
  const [maxPct, setMaxPct] = useState("");
  const [band, setBand] = useState("");
  const [stacking, setStacking] = useState("");
  const [cap, setCap] = useState("");
  const [uncapped, setUncapped] = useState(false);
  const [priority, setPriority] = useState("");
  const [description, setDescription] = useState("");
  const ceiling = kind === CEILING;
  // A percentage rule has a range and no band; a band-ceiling rule has a band and no range. Numbers
  // are sent as typed (the backend validates them); when editing, only what changed is sent.
  const values = {
    name, kind, scope, stacking, description, uncapped,
    min_pct: ceiling || !minPct.trim() ? null : minPct.trim(),
    max_pct: ceiling || !maxPct.trim() ? null : maxPct.trim(),
    ceiling_band: ceiling && band ? Number(band) : null,
    cap_pct: uncapped || !cap.trim() ? null : cap.trim(),
    priority: priority.trim() ? priority.trim() : null,
  };
  const body = existing
    ? changed({
      name: existing.name, kind: existing.kind, scope: existing.scope, stacking: existing.stacking,
      description: existing.description, uncapped: existing.uncapped, min_pct: existing.min_pct, max_pct: existing.max_pct,
      ceiling_band: existing.ceiling_band?.id ?? null, cap_pct: existing.cap_pct,
      priority: existing.priority === null ? null : String(existing.priority),
    }, values)
    : { ...values, code };
  const save = useCfgMutation(
    () => (existing ? cfgApi.updateDeduction(existing.id, body) : cfgApi.createDeduction(plan.id, body)), () => onClose(),
  );
  useEffect(() => {
    if (!open) return;
    setCode(existing?.code ?? ""); setName(existing?.name ?? ""); setKind(existing?.kind ?? "PERCENT_RANGE");
    setScope(existing?.scope ?? ""); setMinPct(existing?.min_pct ?? ""); setMaxPct(existing?.max_pct ?? "");
    setBand(existing?.ceiling_band ? String(existing.ceiling_band.id) : ""); setStacking(existing?.stacking ?? "");
    setCap(existing?.cap_pct ?? ""); setUncapped(existing?.uncapped ?? false);
    setPriority(existing?.priority ? String(existing.priority) : ""); setDescription(existing?.description ?? "");
    save.reset();
  }, [open, existing?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(save.error);
  const missing = (!existing && !code.trim()) || !name.trim() || (!!existing && Object.keys(body).length === 0);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="deduction-title">
      <DialogTitle id="deduction-title">{existing ? `Edit deduction rule ${existing.code}` : "Add deduction rule"}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {save.error instanceof ApiError && Object.keys(errors).length === 0 && <Alert severity="error">{save.error.message}</Alert>}
          <TextField size="small" label="Code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} disabled={!!existing}
            error={!!errors.code} helperText={errors.code ?? (existing ? "The code never changes." : undefined)} />
          <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} error={!!errors.name} helperText={errors.name} />
          <TextField size="small" select label="Kind" value={kind} onChange={(e) => setKind(String(e.target.value))}
            error={!!errors.kind} helperText={errors.kind}>
            {DEDUCTION_KINDS.map(([value, text]) => <MenuItem key={value} value={value}>{text}</MenuItem>)}
          </TextField>
          {ceiling ? (
            <TextField size="small" select label="Ceiling band" value={band} onChange={(e) => setBand(String(e.target.value))}
              error={!!errors.ceiling_band}
              helperText={errors.ceiling_band ?? (plan.band_scheme ? "A band of this plan's band scheme." : "Choose the plan's band scheme first.")}>
              <MenuItem value="">Choose later</MenuItem>
              {(scheme?.bands ?? []).map((b) => <MenuItem key={b.id} value={String(b.id)}>{b.name}</MenuItem>)}
            </TextField>
          ) : (
            <Stack direction="row" spacing={2}>
              <TextField size="small" label="Minimum %" value={minPct} onChange={(e) => setMinPct(e.target.value)} sx={{ flex: 1 }}
                inputProps={{ inputMode: "decimal" }} error={!!errors.min_pct} helperText={errors.min_pct} />
              <TextField size="small" label="Maximum %" value={maxPct} onChange={(e) => setMaxPct(e.target.value)} sx={{ flex: 1 }}
                inputProps={{ inputMode: "decimal" }} error={!!errors.max_pct} helperText={errors.max_pct} />
            </Stack>
          )}
          <TextField size="small" select label="Scope" value={scope} onChange={(e) => setScope(String(e.target.value))}
            error={!!errors.scope} helperText={errors.scope ?? "Required before activation."}>
            <MenuItem value="">Choose later</MenuItem>
            {DEDUCTION_SCOPES.map(([value, text]) => <MenuItem key={value} value={value}>{text}</MenuItem>)}
          </TextField>
          <TextField size="small" select label="Stacking" value={stacking} onChange={(e) => setStacking(String(e.target.value))}
            error={!!errors.stacking} helperText={errors.stacking ?? "Required before activation."}>
            <MenuItem value="">Choose later</MenuItem>
            {DEDUCTION_STACKING.map(([value, text]) => <MenuItem key={value} value={value}>{text}</MenuItem>)}
          </TextField>
          <Stack direction="row" spacing={2} sx={{ alignItems: "flex-start" }}>
            <TextField size="small" label="Cap %" value={uncapped ? "" : cap} disabled={uncapped} onChange={(e) => setCap(e.target.value)}
              sx={{ flex: 1 }} inputProps={{ inputMode: "decimal" }} error={!!errors.cap_pct}
              helperText={errors.cap_pct ?? "Set a cap or mark the rule uncapped before activation."} />
            <FormControlLabel control={<Checkbox checked={uncapped} onChange={(e) => setUncapped(e.target.checked)} />} label="Uncapped" />
          </Stack>
          <TextField size="small" label="Priority" value={priority} onChange={(e) => setPriority(e.target.value)}
            inputProps={{ inputMode: "numeric" }} error={!!errors.priority}
            helperText={errors.priority ?? "1 or more, unique within the plan (required before activation)."} />
          <TextField size="small" label="Description (optional)" value={description} onChange={(e) => setDescription(e.target.value)}
            multiline minRows={2} error={!!errors.description} helperText={errors.description} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={missing || save.isPending} onClick={() => save.mutate()}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

/** Phase 7.5A: one plan version. HR edits a DRAFT (details, KPI lines, components, deduction
 * rules), clones any KRA version and deletes a draft; Admin activates a complete DRAFT and retires an
 * ACTIVE version. ACTIVE and RETIRED versions are read-only for everyone. Legacy (0-100)
 * versions are shown read-only; Admin may still retire an active one. */
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Link, MenuItem, Paper, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../../api/apiClient";
import {
  PERM, type KpiCfgComponent, type KpiCfgDeductionRule, type KpiCfgLine, type KpiCfgPlanDetail,
} from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DateTimeText } from "../../components/DateTimeText";
import { cfgApi, cfgErrors, cfgKeys, changed, useBandSchemes, useCfgMutation, usePlan, useReadiness } from "./api";
import { ComponentDialog } from "./ComponentDialog";
import { DeductionRuleDialog } from "./DeductionRuleDialog";
import {
  ConfirmDialog, Fact, LockedNotice, ModelChip, RetireDialog, StatusChip,
} from "./LifecycleDialogs";
import {
  DEDUCTION_KINDS, DEDUCTION_SCOPES, DEDUCTION_STACKING, labelOf, MANUAL_MATCHES, period, plain, planTitle,
  SOURCE_TYPES, STACKING_METHODS, TASK_SCOPES, VERIFICATION_POLICIES,
} from "./labels";
import { PlanLineDialog } from "./PlanLineDialog";
import { ReadinessPanel } from "./ReadinessPanel";

type Pending = "activate" | "retire" | "clone" | "delete" | null;

export function PlanEditorPage() {
  const id = Number(useParams().id);
  const valid = Number.isInteger(id) && id > 0;
  const { data: plan, error } = usePlan(id);
  if (!valid) return <Alert severity="error">This plan version does not exist.</Alert>;
  if (error) {
    return (
      <Stack spacing={2}>
        <BackLink />
        {error instanceof ApiError && error.status === 404
          ? <Alert severity="error">This plan version does not exist.</Alert>
          : <ApiErrorAlert error={error} />}
      </Stack>
    );
  }
  if (!plan) return <Typography color="text.secondary">Loading…</Typography>;
  return <PlanEditor plan={plan} />;
}

function BackLink() {
  return <Link component={RouterLink} to="/performance/config?tab=plans">← KRA configuration</Link>;
}

function PlanEditor({ plan }: { plan: KpiCfgPlanDetail }) {
  const { hasPerm } = useAuth();
  const navigate = useNavigate();
  const kra = plan.calculation_model === "KRA_POINTS";
  const draft = plan.status === "DRAFT";
  const canEdit = hasPerm(PERM.configureKpis) && kra && draft; // HR, DRAFT only
  const canClone = hasPerm(PERM.configureKpis) && kra;
  const canActivate = hasPerm(PERM.approveKpiConfig) && kra && draft;
  const canRetire = hasPerm(PERM.approveKpiConfig) && plan.status === "ACTIVE"; // legacy or KRA (P13)
  const [pending, setPending] = useState<Pending>(null);
  const [editingDetails, setEditingDetails] = useState(false);
  const [line, setLine] = useState<KpiCfgLine | "new" | null>(null);
  const [componentOf, setComponentOf] = useState<{ line: KpiCfgLine; component: KpiCfgComponent | "new" } | null>(null);
  const [deduction, setDeduction] = useState<KpiCfgDeductionRule | "new" | null>(null);
  // Which edit dialog is open; its item stays set while it closes (no title flicker).
  const [editor, setEditor] = useState<"line" | "component" | "deduction" | null>(null);
  const openLine = (value: KpiCfgLine | "new") => { setLine(value); setEditor("line"); };
  const openComponent = (value: { line: KpiCfgLine; component: KpiCfgComponent | "new" }) => { setComponentOf(value); setEditor("component"); };
  const openDeduction = (value: KpiCfgDeductionRule | "new") => { setDeduction(value); setEditor("deduction"); };
  // The item stays set while its dialog closes (no "Delete undefined?" during the animation).
  const [removing, setRemoving] = useState<{ label: string; run: () => Promise<unknown> } | null>(null);
  const [removeOpen, setRemoveOpen] = useState(false);
  const askRemove = (label: string, run: () => Promise<unknown>) => { setRemoving({ label, run }); setRemoveOpen(true); };

  const close = () => setPending(null);
  const activate = useCfgMutation(() => cfgApi.activatePlan(plan.id), close);
  const retire = useCfgMutation((body: { last_day: string; reason: string }) => cfgApi.retirePlan(plan.id, body), close);
  const clone = useCfgMutation(() => cfgApi.clonePlan(plan.id),
    (copy: KpiCfgPlanDetail) => { close(); navigate(`/performance/config/plans/${copy.id}`); });
  // The deleted draft's own queries are dropped (not reloaded into a 404) before leaving the page.
  const remove = useCfgMutation(() => cfgApi.deletePlan(plan.id), () => navigate("/performance/config?tab=plans"),
    [cfgKeys.plan(plan.id), cfgKeys.readiness(plan.id)]);
  const removeChild = useCfgMutation(() => (removing as { run: () => Promise<unknown> }).run(), () => setRemoveOpen(false));

  return (
    <Stack spacing={3}>
      <BackLink />
      <Stack direction="row" spacing={2} sx={{ justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap" }}>
        <Stack spacing={1}>
          <Typography variant="h2" component="h1">{plan.name}</Typography>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <Typography color="text.secondary">{plan.configuration} v{plan.version}</Typography>
            <StatusChip status={plan.status} />
            <ModelChip model={plan.calculation_model} />
          </Stack>
        </Stack>
        <Stack direction="row" spacing={1}>
          {canClone && <Button onClick={() => setPending("clone")}>Clone as new draft</Button>}
          {canEdit && <Button color="error" onClick={() => setPending("delete")}>Delete draft</Button>}
          {canActivate && <ActivateButton planId={plan.id} onClick={() => setPending("activate")} />}
          {canRetire && <Button variant="contained" color="warning" onClick={() => setPending("retire")}>Retire</Button>}
        </Stack>
      </Stack>
      <LockedNotice status={plan.status} legacy={!kra} canClone={canClone} />

      <Paper component="section" aria-label="Plan details" sx={{ p: 2 }}>
        <Stack spacing={1}>
          <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
            <Typography variant="h6" component="h2">Details</Typography>
            {canEdit && <Button size="small" onClick={() => setEditingDetails(true)}>Edit details</Button>}
          </Stack>
          <Fact label="Effective">{period(plan.effective_from, plan.effective_to)}</Fact>
          <Fact label="Band scheme">{plan.band_scheme ? `${plan.band_scheme.name} (${plan.band_scheme.code} v${plan.band_scheme.version}, ${plan.band_scheme.status.toLowerCase()})` : "—"}</Fact>
          {kra && <Fact label="Task credit: on time / late / overdue">{plain(plan.credit_on_time)} / {plain(plan.credit_late)} / {plain(plan.credit_overdue)}</Fact>}
          {kra && <Fact label="Stacking deductions combine">{labelOf(STACKING_METHODS, plan.deduction_stacking_method)}</Fact>}
          {plan.activated_at && <Fact label="Activated"><DateTimeText value={plan.activated_at} /></Fact>}
          {plan.retired_at && <Fact label="Retired"><DateTimeText value={plan.retired_at} /> — {plan.retire_reason}</Fact>}
        </Stack>
      </Paper>

      {kra && <ReadinessPanel planId={plan.id} />}

      <Stack component="section" aria-label="KPI lines" spacing={2}>
        <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
          <Typography variant="h6" component="h2">KPI lines</Typography>
          {canEdit && <Button variant="outlined" onClick={() => openLine("new")}>Add KPI line</Button>}
        </Stack>
        {plan.lines.length === 0 && <Typography color="text.secondary">No KPI lines yet.</Typography>}
        {plan.lines.map((l) => (
          <Paper key={l.id} sx={{ p: 2 }}>
            <Stack spacing={1}>
              <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap" }}>
                <Typography variant="subtitle1" component="h3">
                  {l.name} — {plain(l.weight)} points
                </Typography>
                {canEdit && (
                  <Stack direction="row" spacing={1}>
                    <Button size="small" onClick={() => openLine(l)} aria-label={`Edit ${l.name}`}>Edit</Button>
                    <Button size="small" onClick={() => openComponent({ line: l, component: "new" })} aria-label={`Add component to ${l.name}`}>Add component</Button>
                    <Button size="small" color="error" aria-label={`Delete ${l.name}`}
                      onClick={() => askRemove(`the KPI line ${l.name} and its components`, () => cfgApi.deleteLine(l.id))}>Delete</Button>
                  </Stack>
                )}
              </Stack>
              <Typography variant="body2" color="text.secondary">
                KPI {l.kpi.name} · Benchmark rule {l.scoring_rule ? `${l.scoring_rule.code} v${l.scoring_rule.version} (${l.scoring_rule.status.toLowerCase()})` : "not chosen"} · Position {l.position}
              </Typography>
              <Table size="small" aria-label={`Components of ${l.name}`}>
                <TableHead>
                  <TableRow>
                    <TableCell>Component</TableCell><TableCell>Source</TableCell><TableCell>Share</TableCell>
                    <TableCell>Task scope</TableCell><TableCell>Manual matching</TableCell><TableCell>Verification</TableCell>
                    {canEdit && <TableCell />}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {l.components.length === 0 && (
                    <TableRow><TableCell colSpan={canEdit ? 7 : 6}><Typography variant="body2" color="text.secondary">No components yet.</Typography></TableCell></TableRow>
                  )}
                  {l.components.map((c) => {
                    const name = c.label || c.responsibility?.name || "—";
                    return (
                      <TableRow key={c.id}>
                        <TableCell>{name}</TableCell>
                        <TableCell>{labelOf(SOURCE_TYPES, c.source_type)}</TableCell>
                        <TableCell>{plain(c.contribution_share)}</TableCell>
                        <TableCell>{labelOf(TASK_SCOPES, c.task_scope)}</TableCell>
                        <TableCell>{labelOf(MANUAL_MATCHES, c.manual_match)}</TableCell>
                        <TableCell>{labelOf(VERIFICATION_POLICIES, c.verification_policy)}</TableCell>
                        {canEdit && (
                          <TableCell>
                            <Stack direction="row" spacing={1}>
                              <Button size="small" onClick={() => openComponent({ line: l, component: c })} aria-label={`Edit component ${name}`}>Edit</Button>
                              <Button size="small" color="error" aria-label={`Delete component ${name}`}
                                onClick={() => askRemove(`the component ${name}`, () => cfgApi.deleteComponent(c.id))}>Delete</Button>
                            </Stack>
                          </TableCell>
                        )}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </Stack>
          </Paper>
        ))}
      </Stack>

      {kra && (
        <Stack component="section" aria-label="Deduction rules" spacing={2}>
          <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
            <Typography variant="h6" component="h2">Deduction rules</Typography>
            {canEdit && <Button variant="outlined" onClick={() => openDeduction("new")}>Add deduction rule</Button>}
          </Stack>
          <Paper>
            <Table size="small" aria-label="Deduction rules table">
              <TableHead>
                <TableRow>
                  <TableCell>Rule</TableCell><TableCell>Kind</TableCell><TableCell>Range / ceiling</TableCell>
                  <TableCell>Scope</TableCell><TableCell>Stacking</TableCell><TableCell>Cap</TableCell><TableCell>Priority</TableCell>
                  {canEdit && <TableCell />}
                </TableRow>
              </TableHead>
              <TableBody>
                {plan.deduction_rules.length === 0 && (
                  <TableRow><TableCell colSpan={canEdit ? 8 : 7}><Typography variant="body2" color="text.secondary">No deduction rules.</Typography></TableCell></TableRow>
                )}
                {plan.deduction_rules.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell><strong>{r.name}</strong><br /><Typography variant="caption" color="text.secondary">{r.code}</Typography></TableCell>
                    <TableCell>{labelOf(DEDUCTION_KINDS, r.kind)}</TableCell>
                    <TableCell>{r.kind === "BAND_CEILING" ? (r.ceiling_band?.name ?? "—") : `${plain(r.min_pct)}–${plain(r.max_pct)} %`}</TableCell>
                    <TableCell>{labelOf(DEDUCTION_SCOPES, r.scope)}</TableCell>
                    <TableCell>{labelOf(DEDUCTION_STACKING, r.stacking)}</TableCell>
                    <TableCell>{r.uncapped ? "Uncapped" : r.cap_pct ? `${plain(r.cap_pct)} %` : "—"}</TableCell>
                    <TableCell>{r.priority ?? "—"}</TableCell>
                    {canEdit && (
                      <TableCell>
                        <Stack direction="row" spacing={1}>
                          <Button size="small" onClick={() => openDeduction(r)} aria-label={`Edit deduction ${r.code}`}>Edit</Button>
                          <Button size="small" color="error" aria-label={`Delete deduction ${r.code}`}
                            onClick={() => askRemove(`the deduction rule ${r.code}`, () => cfgApi.deleteDeduction(r.id))}>Delete</Button>
                        </Stack>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Paper>
        </Stack>
      )}

      <PlanDetailsDialog plan={plan} open={editingDetails} onClose={() => setEditingDetails(false)} />
      <PlanLineDialog open={editor === "line"} plan={plan} line={line} onClose={() => setEditor(null)} />
      <ComponentDialog open={editor === "component"} line={componentOf?.line ?? null} component={componentOf?.component ?? null}
        onClose={() => setEditor(null)} />
      <DeductionRuleDialog open={editor === "deduction"} plan={plan} rule={deduction} onClose={() => setEditor(null)} />
      <ConfirmDialog open={removeOpen} title="Delete from this draft?" confirmLabel="Delete" color="error"
        mutation={removeChild} onClose={() => setRemoveOpen(false)}>
        Delete {removing?.label}? This only changes the draft and is recorded in the audit log.
      </ConfirmDialog>
      <ConfirmDialog open={pending === "activate"} title={`Activate ${planTitle(plan)}?`} confirmLabel="Activate" color="success"
        mutation={activate} onClose={close}>
        Effective {period(plan.effective_from, plan.effective_to)}, for the employees whose department and role
        default, or HR override, points to this plan. Once active it can never be edited again; changes need a new
        version. The server checks everything again before activating.
      </ConfirmDialog>
      <ConfirmDialog open={pending === "clone"} title={`Clone ${planTitle(plan)}?`} confirmLabel="Clone" mutation={clone} onClose={close}>
        A new draft is created with the next version number, copying the KPI lines, components and deduction rules.
        This version does not change.
      </ConfirmDialog>
      <ConfirmDialog open={pending === "delete"} title={`Delete the draft ${planTitle(plan)}?`} confirmLabel="Delete draft" color="error"
        mutation={remove} onClose={close}>
        The draft, its KPI lines, components and deduction rules are deleted. This is recorded in the audit log.
      </ConfirmDialog>
      <RetireDialog open={pending === "retire"} title={`Retire ${planTitle(plan)}`} mutation={retire} onClose={close} />
    </Stack>
  );
}

/** Admin's Activate button: enabled only when the server's readiness check says ready; otherwise
 * the reason is shown next to it (a disabled button cannot show a tooltip). */
function ActivateButton({ planId, onClick }: { planId: number; onClick: () => void }) {
  const { data, error } = useReadiness(planId);
  const reason = error ? "Readiness could not be checked." : data && !data.ready ? "Not ready: see Activation readiness." : null;
  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
      {reason && <Typography variant="caption" color="text.secondary">{reason}</Typography>}
      <Button variant="contained" color="success" disabled={!data?.ready} onClick={onClick}>Activate</Button>
    </Stack>
  );
}

function PlanDetailsDialog({ plan, open, onClose }: { plan: KpiCfgPlanDetail; open: boolean; onClose: () => void }) {
  const { data: schemes = [] } = useBandSchemes(open);
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [scheme, setScheme] = useState("");
  const [onTime, setOnTime] = useState("");
  const [late, setLate] = useState("");
  const [overdue, setOverdue] = useState("");
  const [stacking, setStacking] = useState("");
  const stored = {
    name: plan.name, effective_from: plan.effective_from, effective_to: plan.effective_to,
    band_scheme: plan.band_scheme?.id ?? null, deduction_stacking_method: plan.deduction_stacking_method,
    credit_on_time: plan.credit_on_time, credit_late: plan.credit_late, credit_overdue: plan.credit_overdue,
  };
  // Only what changed is sent, so an untouched value (for example a band scheme retired meanwhile)
  // is never re-validated. A credit that is still unset may stay empty; a stored credit cannot be
  // removed (the API has no way to clear it), so emptying one is refused here.
  const credit = (value: string, current: string | null) => (value.trim() ? value.trim() : current);
  const body = changed(stored, {
    name, effective_from: from, effective_to: to || null, band_scheme: scheme ? Number(scheme) : null,
    deduction_stacking_method: stacking,
    credit_on_time: credit(onTime, stored.credit_on_time), credit_late: credit(late, stored.credit_late),
    credit_overdue: credit(overdue, stored.credit_overdue),
  });
  const clearedCredit = (value: string, current: string | null) => current !== null && !value.trim();
  const save = useCfgMutation(() => cfgApi.updatePlan(plan.id, body), () => onClose());
  useEffect(() => {
    if (!open) return;
    setName(plan.name); setFrom(plan.effective_from); setTo(plan.effective_to ?? "");
    setScheme(plan.band_scheme ? String(plan.band_scheme.id) : "");
    setOnTime(plan.credit_on_time ?? ""); setLate(plan.credit_late ?? ""); setOverdue(plan.credit_overdue ?? "");
    setStacking(plan.deduction_stacking_method ?? ""); save.reset();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(save.error);
  const creditErrors = {
    on_time: clearedCredit(onTime, stored.credit_on_time), late: clearedCredit(late, stored.credit_late),
    overdue: clearedCredit(overdue, stored.credit_overdue),
  };
  const CREDIT_REQUIRED = "Enter a value (a stored credit cannot be removed).";
  const missing = !name.trim() || !from || Object.values(creditErrors).some(Boolean) || Object.keys(body).length === 0;
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="plan-details-title">
      <DialogTitle id="plan-details-title">Edit draft details</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {save.error instanceof ApiError && Object.keys(errors).length === 0 && <Alert severity="error">{save.error.message}</Alert>}
          <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} error={!!errors.name} helperText={errors.name} />
          <Stack direction="row" spacing={2}>
            <TextField size="small" type="date" label="Effective from" value={from} onChange={(e) => setFrom(e.target.value)} sx={{ flex: 1 }}
              InputLabelProps={{ shrink: true }} error={!!errors.effective_from}
              helperText={errors.effective_from ?? "Must be in the future when Admin activates."} />
            <TextField size="small" type="date" label="Effective to (optional)" value={to} onChange={(e) => setTo(e.target.value)} sx={{ flex: 1 }}
              InputLabelProps={{ shrink: true }} error={!!errors.effective_to} helperText={errors.effective_to} />
          </Stack>
          <TextField size="small" select label="Band scheme" value={scheme} onChange={(e) => setScheme(String(e.target.value))}
            error={!!errors.band_scheme} helperText={errors.band_scheme}>
            <MenuItem value="">None yet</MenuItem>
            {schemes.filter((s) => s.status !== "RETIRED" || String(s.id) === scheme).map((s) => (
              <MenuItem key={s.id} value={String(s.id)}>{s.name} ({s.code} v{s.version}, {s.status.toLowerCase()})</MenuItem>
            ))}
          </TextField>
          <Stack direction="row" spacing={2}>
            <TextField size="small" label="Credit on time" value={onTime} onChange={(e) => setOnTime(e.target.value)} sx={{ flex: 1 }}
              inputProps={{ inputMode: "decimal" }} error={!!errors.credit_on_time || creditErrors.on_time}
              helperText={errors.credit_on_time ?? (creditErrors.on_time ? CREDIT_REQUIRED : undefined)} />
            <TextField size="small" label="Credit late" value={late} onChange={(e) => setLate(e.target.value)} sx={{ flex: 1 }}
              inputProps={{ inputMode: "decimal" }} error={!!errors.credit_late || creditErrors.late}
              helperText={errors.credit_late ?? (creditErrors.late ? CREDIT_REQUIRED : undefined)} />
            <TextField size="small" label="Credit overdue" value={overdue} onChange={(e) => setOverdue(e.target.value)} sx={{ flex: 1 }}
              inputProps={{ inputMode: "decimal" }} error={!!errors.credit_overdue || creditErrors.overdue}
              helperText={errors.credit_overdue ?? (creditErrors.overdue ? CREDIT_REQUIRED : undefined)} />
          </Stack>
          <TextField size="small" select label="Stacking deductions combine" value={stacking} onChange={(e) => setStacking(String(e.target.value))}
            error={!!errors.deduction_stacking_method} helperText={errors.deduction_stacking_method ?? "Required before activation when the plan has deduction rules."}>
            <MenuItem value="">Choose later</MenuItem>
            {STACKING_METHODS.map(([value, text]) => <MenuItem key={value} value={value}>{text}</MenuItem>)}
          </TextField>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={missing || save.isPending} onClick={() => save.mutate()}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

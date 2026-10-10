/** Phase 7.5A: one band scheme version (band names and their minimum points on the 10-point
 * scale). HR edits a DRAFT (bands are replaced as a whole) and clones any version; Admin activates
 * a DRAFT and retires an ACTIVE version. ACTIVE and RETIRED versions are read-only for everyone. */
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, Link, Paper, Stack, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from "@mui/material";
import { useEffect, useState } from "react";
import { Link as RouterLink, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../../api/apiClient";
import { PERM, type KpiCfgBandScheme } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DateTimeText } from "../../components/DateTimeText";
import { cfgApi, cfgErrors, changed, useBandScheme, useCfgMutation } from "./api";
import { ConfirmDialog, Fact, LockedNotice, RetireDialog, StatusChip } from "./LifecycleDialogs";
import { period, plain } from "./labels";

export interface BandRow { name: string; min_points: string; }

/** Editable band rows. Values are sent as typed; the backend validates them. */
export function BandsEditor({
  bands, onChange, errors,
}: { bands: BandRow[]; onChange: (bands: BandRow[]) => void; errors: Record<string, string> }) {
  const set = (index: number, key: keyof BandRow, value: string) =>
    onChange(bands.map((b, i) => (i === index ? { ...b, [key]: value } : b)));
  return (
    <Stack spacing={1} role="group" aria-label="Bands">
      {bands.map((band, index) => (
        <Stack key={index} direction="row" spacing={1} sx={{ alignItems: "flex-start" }}>
          <TextField size="small" label={`Band ${index + 1}: name`} value={band.name} onChange={(e) => set(index, "name", e.target.value)}
            sx={{ flex: 2 }} error={!!errors[`bands.${index}`] || !!errors[`bands.${index}.name`]}
            helperText={errors[`bands.${index}`] ?? errors[`bands.${index}.name`]} />
          <TextField size="small" label={`Band ${index + 1}: from points`} value={band.min_points}
            onChange={(e) => set(index, "min_points", e.target.value)} sx={{ flex: 1 }} inputProps={{ inputMode: "decimal" }}
            error={!!errors[`bands.${index}.min_points`]} helperText={errors[`bands.${index}.min_points`]} />
          <IconButton aria-label={`Remove band ${index + 1}`} onClick={() => onChange(bands.filter((_, i) => i !== index))}>×</IconButton>
        </Stack>
      ))}
      {errors.bands && <Typography variant="caption" color="error">{errors.bands}</Typography>}
      <div><Button size="small" onClick={() => onChange([...bands, { name: "", min_points: "" }])}>Add band</Button></div>
    </Stack>
  );
}

export function BandSchemeEditorPage() {
  const id = Number(useParams().id);
  const valid = Number.isInteger(id) && id > 0;
  const { data: scheme, error } = useBandScheme(valid ? id : null);
  const back = <Link component={RouterLink} to="/performance/config?tab=band-schemes">← KRA configuration</Link>;
  if (!valid || (error instanceof ApiError && error.status === 404)) {
    return <Stack spacing={2}>{back}<Alert severity="error">This band scheme does not exist.</Alert></Stack>;
  }
  if (error) return <Stack spacing={2}>{back}<ApiErrorAlert error={error} /></Stack>;
  if (!scheme) return <Typography color="text.secondary">Loading…</Typography>;
  return <Stack spacing={3}>{back}<BandSchemeEditor scheme={scheme} /></Stack>;
}

function BandSchemeEditor({ scheme }: { scheme: KpiCfgBandScheme }) {
  const { hasPerm } = useAuth();
  const navigate = useNavigate();
  const draft = scheme.status === "DRAFT";
  const canEdit = hasPerm(PERM.configureKpis) && draft;
  const canClone = hasPerm(PERM.configureKpis);
  const canActivate = hasPerm(PERM.approveKpiConfig) && draft;
  const canRetire = hasPerm(PERM.approveKpiConfig) && scheme.status === "ACTIVE";
  const [pending, setPending] = useState<"edit" | "activate" | "retire" | "clone" | null>(null);
  const close = () => setPending(null);
  const activate = useCfgMutation(() => cfgApi.activateBandScheme(scheme.id), close);
  const retire = useCfgMutation((body: { last_day: string; reason: string }) => cfgApi.retireBandScheme(scheme.id, body), close);
  const clone = useCfgMutation(() => cfgApi.cloneBandScheme(scheme.id),
    (copy: KpiCfgBandScheme) => { close(); navigate(`/performance/config/band-schemes/${copy.id}`); });
  const title = `${scheme.name} (${scheme.code} v${scheme.version})`;
  const bands = [...scheme.bands].sort((a, b) => a.position - b.position);
  return (
    <>
      <Stack direction="row" spacing={2} sx={{ justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap" }}>
        <Stack spacing={1}>
          <Typography variant="h2" component="h1">{scheme.name}</Typography>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <Typography color="text.secondary">Band scheme {scheme.code} v{scheme.version}</Typography>
            <StatusChip status={scheme.status} />
          </Stack>
        </Stack>
        <Stack direction="row" spacing={1}>
          {canClone && <Button onClick={() => setPending("clone")}>Clone as new draft</Button>}
          {canEdit && <Button variant="outlined" onClick={() => setPending("edit")}>Edit draft</Button>}
          {canActivate && <Button variant="contained" color="success" onClick={() => setPending("activate")}>Activate</Button>}
          {canRetire && <Button variant="contained" color="warning" onClick={() => setPending("retire")}>Retire</Button>}
        </Stack>
      </Stack>
      <LockedNotice status={scheme.status} canClone={canClone} />
      <Paper component="section" aria-label="Band scheme details" sx={{ p: 2 }}>
        <Stack spacing={1}>
          <Fact label="Effective">{period(scheme.effective_from, scheme.effective_to)}</Fact>
          {scheme.activated_at && <Fact label="Activated"><DateTimeText value={scheme.activated_at} /></Fact>}
          {scheme.retired_at && <Fact label="Retired"><DateTimeText value={scheme.retired_at} /> — {scheme.retire_reason}</Fact>}
        </Stack>
      </Paper>
      <Paper>
        <Table size="small" aria-label="Bands table">
          <TableHead><TableRow><TableCell>Band</TableCell><TableCell>From points (at or above)</TableCell></TableRow></TableHead>
          <TableBody>
            {bands.length === 0 && <TableRow><TableCell colSpan={2}>No bands yet.</TableCell></TableRow>}
            {bands.map((b) => <TableRow key={b.id}><TableCell>{b.name}</TableCell><TableCell>{plain(b.min_points)}</TableCell></TableRow>)}
          </TableBody>
        </Table>
      </Paper>
      <EditSchemeDialog scheme={scheme} open={pending === "edit"} onClose={close} />
      <ConfirmDialog open={pending === "activate"} title={`Activate ${title}?`} confirmLabel="Activate" color="success"
        mutation={activate} onClose={close}>
        Once active, this band scheme can never be edited again. The server checks it before activating.
      </ConfirmDialog>
      <ConfirmDialog open={pending === "clone"} title={`Clone ${title}?`} confirmLabel="Clone" mutation={clone} onClose={close}>
        A new draft is created with the next version number and the same bands. This version does not change.
      </ConfirmDialog>
      <RetireDialog open={pending === "retire"} title={`Retire ${title}`} mutation={retire} onClose={close} />
    </>
  );
}

function EditSchemeDialog({ scheme, open, onClose }: { scheme: KpiCfgBandScheme; open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [bands, setBands] = useState<BandRow[]>([]);
  const stored = {
    name: scheme.name, effective_from: scheme.effective_from, effective_to: scheme.effective_to,
    bands: [...scheme.bands].sort((a, b) => a.position - b.position).map((b) => ({ name: b.name, min_points: b.min_points })),
  };
  // Only what changed is sent. The bands are replaced as a whole on the server (and refused while
  // a deduction rule uses one as its ceiling), so they are sent only when a band really changed -
  // then with their positions in the order shown.
  const body = changed(stored, { name, effective_from: from, effective_to: to || null, bands });
  if (body.bands) body.bands = bands.map((b, index) => ({ ...b, position: index + 1 }));
  const save = useCfgMutation(() => cfgApi.updateBandScheme(scheme.id, body), () => onClose());
  useEffect(() => {
    if (!open) return;
    setName(stored.name); setFrom(stored.effective_from); setTo(stored.effective_to ?? "");
    setBands(stored.bands.map((b) => ({ ...b })));
    save.reset();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(save.error);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="scheme-edit-title">
      <DialogTitle id="scheme-edit-title">Edit draft band scheme</DialogTitle>
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
          <BandsEditor bands={bands} onChange={setBands} errors={errors} />
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

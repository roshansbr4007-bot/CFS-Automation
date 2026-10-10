/** Phase 7.5A: band scheme versions. HR creates and clones drafts; Admin activates and retires on
 * the scheme's own page. */
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField, Typography,
} from "@mui/material";
import { useEffect, useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";

import { ApiError } from "../../api/apiClient";
import { PERM, type KpiCfgBandScheme } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { cfgApi, cfgErrors, useBandSchemes, useCfgMutation } from "./api";
import { BandsEditor, type BandRow } from "./BandSchemeEditorPage";
import { StatusChip } from "./LifecycleDialogs";
import { period } from "./labels";

const PAGE = 25;

export function BandSchemesTab() {
  const { hasPerm } = useAuth();
  const canPrepare = hasPerm(PERM.configureKpis);
  const { data = [], isFetching, error } = useBandSchemes();
  const [creating, setCreating] = useState(false);
  const [page, setPage] = useState(0); // the whole list is loaded; the table pages through it
  const columns: Column<KpiCfgBandScheme>[] = [
    { key: "scheme", header: "Band scheme", render: (s) => <Stack><strong>{s.name}</strong><Typography variant="caption" color="text.secondary">{s.code} v{s.version}</Typography></Stack> },
    { key: "status", header: "Status", render: (s) => <StatusChip status={s.status} /> },
    { key: "period", header: "Effective", render: (s) => period(s.effective_from, s.effective_to) },
    { key: "bands", header: "Bands", render: (s) => s.bands.length },
    { key: "open", header: "", render: (s) => (
      <Button size="small" component={RouterLink} to={`/performance/config/band-schemes/${s.id}`}
        aria-label={`Open ${s.code} v${s.version}`}>Open</Button>
    ) },
  ];
  const shown = Math.min(page, Math.max(0, Math.ceil(data.length / PAGE) - 1)); // a list that shrank
  return (
    <Stack spacing={2}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Typography color="text.secondary">The performance bands a monthly KRA total falls into.</Typography>
        {canPrepare && <Button variant="contained" onClick={() => setCreating(true)}>New draft band scheme</Button>}
      </Stack>
      <ApiErrorAlert error={error} />
      <DataTable caption="Band schemes" columns={columns} rows={data.slice(shown * PAGE, (shown + 1) * PAGE)}
        getRowId={(s) => s.id} loading={isFetching} total={data.length} page={shown} onPageChange={setPage} emptyMessage="No band schemes yet." />
      <CreateSchemeDialog open={creating} onClose={() => setCreating(false)} />
    </Stack>
  );
}

function CreateSchemeDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [bands, setBands] = useState<BandRow[]>([]);
  const create = useCfgMutation(() => cfgApi.createBandScheme({
    code, name, effective_from: from, effective_to: to || null, bands,
  }), (scheme: KpiCfgBandScheme) => { onClose(); navigate(`/performance/config/band-schemes/${scheme.id}`); });
  useEffect(() => {
    if (open) { setCode(""); setName(""); setFrom(""); setTo(""); setBands([]); create.reset(); }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(create.error);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="new-scheme-title">
      <DialogTitle id="new-scheme-title">New draft band scheme</DialogTitle>
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
          <BandsEditor bands={bands} onChange={setBands} errors={errors} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!code.trim() || !name.trim() || !from || create.isPending} onClick={() => create.mutate()}>Create draft</Button>
      </DialogActions>
    </Dialog>
  );
}

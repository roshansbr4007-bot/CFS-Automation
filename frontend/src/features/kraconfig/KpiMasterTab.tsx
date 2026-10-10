/** Phase 7.5A: the KPI master. Codes and names are fixed; HR may edit a KPI's description only. */
import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField, Typography,
} from "@mui/material";
import { useEffect, useState } from "react";

import { ApiError } from "../../api/apiClient";
import { PERM, type KpiCfgKpi } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { cfgApi, cfgErrors, useCfgMutation, useKpis } from "./api";

const PAGE = 25;

export function KpiMasterTab() {
  const { hasPerm } = useAuth();
  const canPrepare = hasPerm(PERM.configureKpis);
  const { data = [], isFetching, error } = useKpis();
  const [editing, setEditing] = useState<KpiCfgKpi | null>(null);
  const [open, setOpen] = useState(false); // the KPI stays set while its dialog closes
  const [page, setPage] = useState(0); // the whole list is loaded; the table pages through it
  const columns: Column<KpiCfgKpi>[] = [
    { key: "kpi", header: "KPI", render: (k) => <Stack><strong>{k.name}</strong><Typography variant="caption" color="text.secondary">{k.code}</Typography></Stack> },
    { key: "description", header: "Description", render: (k) => k.description || "—" },
    { key: "active", header: "Status", render: (k) => <Chip size="small" label={k.is_active ? "Active" : "Inactive"} color={k.is_active ? "success" : "default"} /> },
    ...(canPrepare ? [{ key: "edit", header: "", render: (k: KpiCfgKpi) => (
      <Button size="small" onClick={() => { setEditing(k); setOpen(true); }} aria-label={`Edit description of ${k.name}`}>Edit description</Button>
    ) }] : []),
  ];
  const shown = Math.min(page, Math.max(0, Math.ceil(data.length / PAGE) - 1)); // a list that shrank
  return (
    <Stack spacing={2}>
      <Typography color="text.secondary">The KPIs plans are built from. Codes and names never change.</Typography>
      <ApiErrorAlert error={error} />
      <DataTable caption="KPIs" columns={columns} rows={data.slice(shown * PAGE, (shown + 1) * PAGE)}
        getRowId={(k) => k.id} loading={isFetching} total={data.length} page={shown} onPageChange={setPage} emptyMessage="No KPIs." />
      <DescriptionDialog open={open} kpi={editing} onClose={() => setOpen(false)} />
    </Stack>
  );
}

function DescriptionDialog({ open, kpi, onClose }: { open: boolean; kpi: KpiCfgKpi | null; onClose: () => void }) {
  const [description, setDescription] = useState("");
  const save = useCfgMutation(() => cfgApi.updateKpi((kpi as KpiCfgKpi).id, { description }), () => onClose());
  useEffect(() => { if (open && kpi) { setDescription(kpi.description); save.reset(); } }, [open, kpi?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const errors = cfgErrors(save.error);
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="kpi-title">
      <DialogTitle id="kpi-title">Description of {kpi?.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {save.error instanceof ApiError && Object.keys(errors).length === 0 && <Alert severity="error">{save.error.message}</Alert>}
          <TextField size="small" label="Description" value={description} onChange={(e) => setDescription(e.target.value)}
            multiline minRows={3} error={!!errors.description} helperText={errors.description} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={save.isPending} onClick={() => save.mutate()}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

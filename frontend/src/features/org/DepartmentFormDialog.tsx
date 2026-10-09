import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Switch, FormControlLabel, TextField } from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { departmentsApi } from "../../api/endpoints";
import type { Department } from "../../api/types";
import { ApiError } from "../../api/apiClient";

export function DepartmentFormDialog({ open, department, onClose }: { open: boolean; department: Department | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [code, setCode] = useState(""); const [name, setName] = useState(""); const [isLive, setIsLive] = useState(false); const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => { if (open) { setCode(department?.code ?? ""); setName(department?.name ?? ""); setIsLive(department?.is_live ?? false); setError(null); } }, [open, department]);
  const mutation = useMutation({ mutationFn: () => department ? departmentsApi.update(department.id, { name, is_live: isLive }) : departmentsApi.create({ code, name, is_live: isLive }), onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["departments"] }); onClose(); } });
  const apiError = mutation.error instanceof ApiError ? mutation.error : error;
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
    <DialogTitle>{department ? "Edit department" : "Add department"}</DialogTitle>
    <DialogContent><Stack spacing={2} sx={{ mt: 1 }}>
      {apiError && <Alert severity="error">{apiError.message}</Alert>}
      <TextField label="Code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} disabled={!!department} helperText={department ? "Department code cannot be changed." : "1–16 capital letters, digits or _."} />
      <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} />
      <FormControlLabel control={<Switch checked={isLive} onChange={(e) => setIsLive(e.target.checked)} />} label="Live department" />
    </Stack></DialogContent>
    <DialogActions><Button onClick={onClose}>Cancel</Button><Button variant="contained" disabled={!name.trim() || (!department && !code.trim()) || mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? "Saving…" : "Save"}</Button></DialogActions>
  </Dialog>;
}

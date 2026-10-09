import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { employeesApi, usersApi } from "../../api/endpoints";
import type { Employee } from "../../api/types";
import { ApiError } from "../../api/apiClient";

export function LinkLoginDialog({ employee, onClose }: { employee: Employee | null; onClose: () => void }) {
  const qc = useQueryClient(); const [search, setSearch] = useState(""); const [userId, setUserId] = useState("");
  const { data } = useQuery({ queryKey: ["login-users", search], queryFn: () => usersApi.list({ page: 1, search, is_active: "true" }), enabled: !!employee });
  const link = useMutation({ mutationFn: () => employeesApi.linkLogin(employee!.id, employee!.version, Number(userId)), onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["employees"] }); onClose(); } });
  const unlink = useMutation({ mutationFn: () => employeesApi.unlinkLogin(employee!.id, employee!.version), onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["employees"] }); onClose(); } });
  const error = (link.error instanceof ApiError ? link.error : unlink.error instanceof ApiError ? unlink.error : null);
  return <Dialog open={!!employee} onClose={onClose} fullWidth maxWidth="sm"><DialogTitle>{employee?.user ? "Employee login" : "Link employee login"}</DialogTitle><DialogContent><Stack spacing={2} sx={{ mt: 1 }}>
    {error && <Alert severity="error">{error.message}</Alert>}
    {employee?.user ? <><Alert severity="info">Linked to {employee.user.email}</Alert><Button color="error" variant="outlined" disabled={unlink.isPending} onClick={() => unlink.mutate()}>Unlink login</Button></> : <>
      <TextField label="Search login by name/email" value={search} onChange={(e) => setSearch(e.target.value)} />
      <TextField select label="Login user" value={userId} onChange={(e) => setUserId(e.target.value)}><MenuItem value="">Select a user</MenuItem>{(data?.results ?? []).map((u) => <MenuItem key={u.id} value={u.id}>{u.full_name || u.email} — {u.email}</MenuItem>)}</TextField>
      <Alert severity="info">The login email must match the employee email.</Alert>
    </>}
  </Stack></DialogContent><DialogActions><Button onClick={onClose}>Close</Button>{!employee?.user && <Button variant="contained" disabled={!userId || link.isPending} onClick={() => link.mutate()}>Link login</Button>}</DialogActions></Dialog>;
}

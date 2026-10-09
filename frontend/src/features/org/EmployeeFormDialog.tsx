import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { departmentsApi, employeesApi } from "../../api/endpoints";
import type { Department, Employee } from "../../api/types";
import { ApiError } from "../../api/apiClient";

export function EmployeeFormDialog({ open, employee, canEdit, onClose }: { open: boolean; employee: Employee | null; canEdit: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: departments = [] } = useQuery({ queryKey: ["departments"], queryFn: departmentsApi.list, enabled: open });
  const { data: managerData } = useQuery({ queryKey: ["employee-managers"], queryFn: () => employeesApi.list({ page: 1, is_active: true }), enabled: open && canEdit });
  const managers = useMemo(() => (managerData?.results ?? []).filter((m) => m.id !== employee?.id), [managerData, employee]);
  const [fullName, setFullName] = useState(""); const [email, setEmail] = useState(""); const [department, setDepartment] = useState(""); const [code, setCode] = useState(""); const [manager, setManager] = useState(""); const [designation, setDesignation] = useState(""); const [joined, setJoined] = useState(""); const [active, setActive] = useState(true);
  useEffect(() => { if (open) { setFullName(employee?.full_name ?? ""); setEmail(employee?.email ?? ""); setDepartment(employee ? String(employee.department.id) : ""); setCode(employee?.employee_code ?? ""); setManager(employee?.reporting_manager ? String(employee.reporting_manager.id) : ""); setDesignation(employee?.designation ?? ""); setJoined(employee?.date_of_joining ?? ""); setActive(employee?.is_active ?? true); } }, [open, employee]);
  const mutation = useMutation({ mutationFn: () => employee ? employeesApi.update(employee.id, { version: employee.version, full_name: fullName, email, department: Number(department), employee_code: code || null, reporting_manager: manager ? Number(manager) : null, designation, date_of_joining: joined || null, is_active: active }) : employeesApi.create({ full_name: fullName, email, department: Number(department), employee_code: code || null, reporting_manager: manager ? Number(manager) : null, designation, date_of_joining: joined || null }), onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["employees"] }); onClose(); } });
  if (!canEdit) return <Dialog open={open} onClose={onClose}><DialogTitle>Employee details</DialogTitle><DialogContent><Stack spacing={1} sx={{ mt: 1 }}><strong>{employee?.full_name}</strong><span>{employee?.email}</span><span>{employee?.designation || "—"}</span><span>Joined: {employee?.date_of_joining || "—"}</span><span>{employee?.department.name}</span><span>{employee?.is_active ? "Active" : "Deactivated"}</span></Stack></DialogContent><DialogActions><Button onClick={onClose}>Close</Button></DialogActions></Dialog>;
  const error = mutation.error instanceof ApiError ? mutation.error : null;
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm"><DialogTitle>{employee ? "Edit employee" : "Add employee"}</DialogTitle><DialogContent><Stack spacing={2} sx={{ mt: 1 }}>
    {error && <Alert severity="error">{error.message}{error.status === 409 ? " Reload the employee and try again." : ""}</Alert>}
    <TextField label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
    <TextField label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
    <TextField select label="Department" value={department} onChange={(e) => setDepartment(e.target.value)}><MenuItem value="">Select department</MenuItem>{departments.map((d: Department) => <MenuItem key={d.id} value={d.id}>{d.code} — {d.name}</MenuItem>)}</TextField>
    <TextField label="Employee code" value={code} onChange={(e) => setCode(e.target.value)} />
    <TextField select label="Reporting manager" value={manager} onChange={(e) => setManager(e.target.value)}><MenuItem value="">No manager</MenuItem>{managers.map((m) => <MenuItem key={m.id} value={m.id}>{m.full_name}</MenuItem>)}</TextField>
    <TextField label="Designation" value={designation} onChange={(e) => setDesignation(e.target.value)} />
    <TextField label="Date of joining" type="date" value={joined} onChange={(e) => setJoined(e.target.value)} InputLabelProps={{ shrink: true }} helperText="Performance history starts from this date." />
    {employee && <TextField select label="Status" value={active ? "true" : "false"} onChange={(e) => setActive(e.target.value === "true")}><MenuItem value="true">Active</MenuItem><MenuItem value="false">Deactivated</MenuItem></TextField>}
  </Stack></DialogContent><DialogActions><Button onClick={onClose}>Cancel</Button><Button variant="contained" disabled={!fullName.trim() || !email.trim() || !department || mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? "Saving…" : "Save"}</Button></DialogActions></Dialog>;
}

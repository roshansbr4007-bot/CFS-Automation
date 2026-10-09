import { Button, Chip, MenuItem, Stack, TextField, Tooltip, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { employeesApi, departmentsApi } from "../../api/endpoints";
import { PERM, type Employee } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { EmployeeFormDialog } from "./EmployeeFormDialog";
import { LinkLoginDialog } from "./LinkLoginDialog";
import { LoginHistoryDialog } from "./LoginHistoryDialog";
import { keepPreviousData } from "@tanstack/react-query";

export function EmployeesPage() {
  const { hasPerm } = useAuth(); const canEdit = hasPerm(PERM.manageEmployees); const canLink = hasPerm(PERM.linkEmployeeLogin);
  const [page, setPage] = useState(0); const [search, setSearch] = useState(""); const [department, setDepartment] = useState(""); const [status, setStatus] = useState(""); const [hasLogin, setHasLogin] = useState("");
  const [form, setForm] = useState<{ open: boolean; employee: Employee | null }>({ open: false, employee: null }); const [link, setLink] = useState<Employee | null>(null); const [history, setHistory] = useState<Employee | null>(null);
  const { data: departments = [] } = useQuery({ queryKey: ["departments"], queryFn: departmentsApi.list });
  const params = { page: page + 1, search, department, is_active: status, has_login: hasLogin };
  const { data, isFetching, error } = useQuery({ queryKey: ["employees", params], queryFn: () => employeesApi.list(params), placeholderData: keepPreviousData });
  const reset = (setter: (v: string) => void) => (value: string) => { setter(value); setPage(0); };
  const columns: Column<Employee>[] = [
    { key: "code", header: "Code", render: (e) => e.employee_code || "—" },
    { key: "name", header: "Employee", render: (e) => <Stack><strong>{e.full_name}</strong><Typography variant="caption" color="text.secondary">{e.email}</Typography></Stack> },
    { key: "department", header: "Department", render: (e) => e.department.code },
    { key: "designation", header: "Designation", render: (e) => e.designation || "—" },
    { key: "joined", header: "Joined", render: (e) => e.date_of_joining || "—" },
    { key: "manager", header: "Manager", render: (e) => e.reporting_manager?.full_name || "—" },
    { key: "status", header: "Status", render: (e) => <Chip size="small" label={e.is_active ? "Active" : "Deactivated"} /> },
    { key: "login", header: "Login", render: (e) => e.user ? <Chip size="small" label="Linked" /> : <Chip size="small" label="Not linked" variant="outlined" /> },
    { key: "actions", header: "Actions", render: (e) => <Stack direction="row" spacing={0.5}>
      <Button size="small" onClick={() => setForm({ open: true, employee: e })}>{canEdit ? "Edit" : "View"}</Button>
      {canLink && <Tooltip title="Link or unlink login"><Button size="small" onClick={() => setLink(e)}>Login</Button></Tooltip>}
      <Button size="small" onClick={() => setHistory(e)}>Logins</Button>
    </Stack> },
  ];
  return <Stack spacing={3}>
    <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ alignItems: { sm: "center" }, justifyContent: "space-between" }}><Typography variant="h2" component="h1">Employees</Typography>{canEdit && <Button variant="contained" onClick={() => setForm({ open: true, employee: null })}>Add employee</Button>}</Stack>
    <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
      <TextField label="Search name, email or code" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
      <TextField select label="Department" value={department} onChange={(e) => reset(setDepartment)(e.target.value)}><MenuItem value="">All departments</MenuItem>{departments.map((d) => <MenuItem key={d.id} value={d.id}>{d.code}</MenuItem>)}</TextField>
      <TextField select label="Status" value={status} onChange={(e) => reset(setStatus)(e.target.value)}><MenuItem value="">All</MenuItem><MenuItem value="true">Active</MenuItem><MenuItem value="false">Deactivated</MenuItem></TextField>
      <TextField select label="Login" value={hasLogin} onChange={(e) => reset(setHasLogin)(e.target.value)}><MenuItem value="">All</MenuItem><MenuItem value="true">Linked</MenuItem><MenuItem value="false">Not linked</MenuItem></TextField>
    </Stack>
    <ApiErrorAlert error={error} />
    <DataTable caption="Employees" columns={columns} rows={data?.results ?? []} getRowId={(e) => e.id} loading={isFetching} total={data?.count ?? 0} page={page} onPageChange={setPage} emptyMessage="No employees match these filters." />
    <EmployeeFormDialog open={form.open} employee={form.employee} canEdit={canEdit} onClose={() => setForm({ open: false, employee: null })} />
    <LinkLoginDialog employee={link} onClose={() => setLink(null)} />
    <LoginHistoryDialog employee={history} onClose={() => setHistory(null)} />
  </Stack>;
}

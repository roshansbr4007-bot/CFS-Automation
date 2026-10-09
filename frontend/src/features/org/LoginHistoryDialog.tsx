import { Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { employeesApi } from "../../api/endpoints";
import type { Employee } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText } from "../../components/DateTimeText";
import type { DailyLogin } from "../../api/types";

export function LoginHistoryDialog({ employee, onClose }: { employee: Employee | null; onClose: () => void }) {
  const { data, isFetching } = useQuery({ queryKey: ["employee-logins", employee?.id], queryFn: () => employeesApi.logins(employee!.id), enabled: !!employee });
  const columns: Column<DailyLogin>[] = [
    { key: "date", header: "Work date", render: (r) => r.work_date },
    { key: "login", header: "First login", render: (r) => <DateTimeText value={r.first_login_at} /> },
  ];
  return <Dialog open={!!employee} onClose={onClose} fullWidth maxWidth="md"><DialogTitle>Login history — {employee?.full_name}</DialogTitle><DialogContent><Stack spacing={2} sx={{ mt: 1 }}><Typography variant="body2" color="text.secondary">Daily login facts recorded by the backend.</Typography><DataTable caption="Login history" columns={columns} rows={data?.results ?? []} getRowId={(r) => r.work_date} loading={isFetching} total={data?.count ?? 0} page={0} onPageChange={() => undefined} emptyMessage="No login records found." /></Stack></DialogContent><DialogActions><Button onClick={onClose}>Close</Button></DialogActions></Dialog>;
}

import { Button, Chip, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { departmentsApi } from "../../api/endpoints";
import type { Department } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText } from "../../components/DateTimeText";
import { DepartmentFormDialog } from "./DepartmentFormDialog";

export function DepartmentsPage() {
  const [dialog, setDialog] = useState<{ open: boolean; department: Department | null }>({ open: false, department: null });
  const { data = [], isFetching, error } = useQuery({ queryKey: ["departments"], queryFn: departmentsApi.list });
  const columns: Column<Department>[] = [
    { key: "code", header: "Code", render: (d) => d.code },
    { key: "name", header: "Name", render: (d) => d.name },
    { key: "live", header: "Status", render: (d) => <Chip size="small" label={d.is_live ? "Live" : "Inactive"} /> },
    { key: "updated", header: "Updated", render: (d) => <DateTimeText value={d.updated_at} /> },
    { key: "actions", header: "", render: (d) => <Button size="small" onClick={() => setDialog({ open: true, department: d })}>Edit</Button> },
  ];
  return <Stack spacing={3}>
    <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between" }}><Typography variant="h2" component="h1">Departments</Typography><Button variant="contained" onClick={() => setDialog({ open: true, department: null })}>Add department</Button></Stack>
    <ApiErrorAlert error={error} />
    <DataTable caption="Departments" columns={columns} rows={data} getRowId={(d) => d.id} loading={isFetching} total={data.length} page={0} onPageChange={() => undefined} emptyMessage="No departments found." />
    <DepartmentFormDialog open={dialog.open} department={dialog.department} onClose={() => setDialog({ open: false, department: null })} />
  </Stack>;
}

import { Alert, Button, Stack, Typography } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { PERM, type OverdueCase } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText } from "../../components/DateTimeText";
import { PRIORITY_LABEL } from "../tasks/labels";
import { formatOverdueMinutes, OVERDUE_CAUSE_LABEL } from "./labels";
import { hasActiveFilters, OverdueFilterBar, type BarFilters } from "./OverdueFilterBar";
import { OverdueStatusChip } from "./parts";
import { useKnownTotal } from "./useKnownTotal";
import { downloadOverdueReport, useOverdueReport, type ReportFormat } from "./reportApi";

/** Every case the backend lets me see (team scope for an Operations Manager, all for HR / Admin),
 * in the backend's report order. Exports are HR / Admin only (the backend enforces this too). */
export function OverdueReportPanel() {
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const canExport = hasPerm(PERM.viewAllOverdueCases);
  const [filters, setFilters] = useState<BarFilters>({});
  const [page, setPage] = useState(0);
  const report = useOverdueReport({ ...filters, page: page + 1 });
  const total = useKnownTotal(report.data?.count);
  const exporter = useMutation({
    mutationFn: ({ format, current }: { format: ReportFormat; current: BarFilters }) => downloadOverdueReport(format, current),
  });
  const changeFilters = (next: BarFilters) => { setPage(0); setFilters(next); exporter.reset(); };

  const columns: Column<OverdueCase>[] = [
    { key: "task", header: "Task", render: (c) => <><strong>{c.task_reference}</strong> {c.task_title}</> },
    { key: "employee", header: "Employee", render: (c) => c.employee.full_name },
    { key: "department", header: "Department", render: (c) => c.department.code },
    { key: "priority", header: "Priority", render: (c) => PRIORITY_LABEL[c.priority] },
    { key: "deadline", header: "Deadline", render: (c) => <DateTimeText value={c.sla_due_at} /> },
    { key: "overdue", header: "Overdue for", render: (c) => formatOverdueMinutes(c.overdue_minutes) },
    { key: "status", header: "Status", render: (c) => <OverdueStatusChip status={c.status} /> },
    { key: "reason", header: "Employee reason", render: (c) => (c.reason_category ? OVERDUE_CAUSE_LABEL[c.reason_category] : "—") },
    { key: "cause", header: "Reviewer's cause", render: (c) => (c.cause ? OVERDUE_CAUSE_LABEL[c.cause] : "—") },
    { key: "open", header: "", render: (c) => <Button size="small" onClick={() => navigate(`/overdue-cases/${c.id}`)}>Open</Button> },
  ];

  return (
    <Stack spacing={2}>
      <OverdueFilterBar fields={["status", "cause", "reason_category", "department", "priority", "date_from", "date_to"]}
        value={filters} onChange={changeFilters} />
      <Stack direction="row" component="section" aria-label="Export" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap" }}>
        {canExport ? (
          <>
            <Button variant="outlined" disabled={exporter.isPending} onClick={() => exporter.mutate({ format: "csv", current: filters })}>Export CSV</Button>
            <Button variant="outlined" disabled={exporter.isPending} onClick={() => exporter.mutate({ format: "excel", current: filters })}>Export Excel</Button>
            <Typography variant="body2" color="text.secondary">
              {exporter.isPending ? "Preparing the export…" : "Exports include every case matching the filters above."}
            </Typography>
          </>
        ) : (
          <Typography variant="body2" color="text.secondary">Exports are available to HR and Admin.</Typography>
        )}
      </Stack>
      {exporter.isSuccess && <Alert severity="success" onClose={() => exporter.reset()}>Downloaded {exporter.data}.</Alert>}
      <ApiErrorAlert error={exporter.error} />
      {report.isError && (
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Stack sx={{ flex: 1 }}><ApiErrorAlert error={report.error} /></Stack>
          <Button onClick={() => void report.refetch()}>Retry</Button>
        </Stack>
      )}
      <DataTable caption="Overdue cases report" columns={columns} rows={report.data?.results ?? []} getRowId={(c) => c.id}
        loading={report.isPending || report.isFetching} total={total} page={page} onPageChange={setPage}
        emptyMessage={report.isError ? "Could not load the report."
          : hasActiveFilters(filters) ? "No overdue cases match the selected filters." : "There are no overdue cases."} />
    </Stack>
  );
}

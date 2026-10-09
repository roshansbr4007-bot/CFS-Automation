import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import type { OverdueCase } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText } from "../../components/DateTimeText";
import { PRIORITY_LABEL } from "../tasks/labels";
import { formatOverdueMinutes, OVERDUE_CAUSE_LABEL } from "./labels";
import { hasActiveFilters, OverdueFilterBar, type BarFilters } from "./OverdueFilterBar";
import { OverdueStatusChip } from "./parts";
import { useKnownTotal } from "./useKnownTotal";
import { useMyOverdueCases } from "./queries";

/** Overdue cases where I am the employee recorded at breach time (my reason is asked for). */
export function MyOverdueCasesPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState<BarFilters>({});
  // The employee scope stays inside useMyOverdueCases (it always adds my own employee id).
  const cases = useMyOverdueCases({ ...filters, page: page + 1 });
  const total = useKnownTotal(cases.data?.count);

  const columns: Column<OverdueCase>[] = [
    { key: "task", header: "Task", render: (c) => <><strong>{c.task_reference}</strong> {c.task_title}</> },
    { key: "department", header: "Department", render: (c) => c.department.code },
    { key: "priority", header: "Priority", render: (c) => PRIORITY_LABEL[c.priority] },
    { key: "deadline", header: "Deadline", render: (c) => <DateTimeText value={c.sla_due_at} /> },
    { key: "overdue", header: "Overdue for", render: (c) => formatOverdueMinutes(c.overdue_minutes) },
    { key: "status", header: "Status", render: (c) => <OverdueStatusChip status={c.status} /> },
    { key: "reason", header: "My reason", render: (c) => (c.reason_category ? OVERDUE_CAUSE_LABEL[c.reason_category] : "Not submitted") },
    { key: "review", header: "Reviewer's cause", render: (c) => (c.cause ? OVERDUE_CAUSE_LABEL[c.cause] : "—") },
    { key: "open", header: "", render: (c) => <Button size="small" onClick={() => navigate(`/overdue-cases/${c.id}`)}>Open</Button> },
  ];

  const waiting = cases.data?.results.filter((c) => c.can_submit).length ?? 0;
  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h2" component="h1">My overdue cases</Typography>
        <Typography color="text.secondary">Tasks that passed their deadline while they were assigned to you.</Typography>
      </Box>
      <OverdueFilterBar fields={["status", "reason_category", "cause", "priority", "date_from", "date_to"]}
        labels={{ reason_category: "My reason" }} value={filters} onChange={(next) => { setPage(0); setFilters(next); }} />
      {waiting > 0 && <Alert severity="warning">{waiting} case{waiting === 1 ? " needs" : "s need"} your reason.</Alert>}
      {cases.isError && (
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Box sx={{ flex: 1 }}><ApiErrorAlert error={cases.error} /></Box>
          <Button onClick={() => void cases.refetch()}>Retry</Button>
        </Stack>
      )}
      <DataTable caption="My overdue cases" columns={columns} rows={cases.data?.results ?? []} getRowId={(c) => c.id}
        loading={cases.isPending || cases.isFetching} total={total} page={page}
        onPageChange={setPage} emptyMessage={cases.isError ? "Could not load your overdue cases."
          : hasActiveFilters(filters) ? "No overdue cases match the selected filters." : "You have no overdue cases."} />
    </Stack>
  );
}

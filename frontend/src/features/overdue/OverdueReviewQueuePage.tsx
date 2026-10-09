import { Box, Button, Stack, Tab, Tabs, Typography } from "@mui/material";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import type { OverdueCase } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText } from "../../components/DateTimeText";
import { PRIORITY_LABEL } from "../tasks/labels";
import { formatOverdueMinutes, OVERDUE_CAUSE_LABEL } from "./labels";
import { hasActiveFilters, OverdueFilterBar, type BarFilters } from "./OverdueFilterBar";
import { OverdueReportPanel } from "./OverdueReportPanel";
import { OverdueStatusChip } from "./parts";
import { useKnownTotal } from "./useKnownTotal";
import { useOverdueReviewQueue } from "./queries";

/** Reviewers' page: the queue of cases waiting for MY review, and (Stage 5.3) the report of all
 * cases the backend lets me see. Only the active tab is rendered (and fetches). */
export function OverdueReviewQueuePage() {
  const [tab, setTab] = useState<"queue" | "report">("queue");
  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h2" component="h1">Overdue review queue</Typography>
        <Typography color="text.secondary">Reasons submitted by employees, waiting for your authoritative decision.</Typography>
      </Box>
      <Tabs value={tab} onChange={(_, next) => setTab(next)} aria-label="Overdue views">
        <Tab value="queue" label="Waiting for my review" />
        <Tab value="report" label="All cases (report)" />
      </Tabs>
      {tab === "queue" ? <ReviewQueue /> : <OverdueReportPanel />}
    </Stack>
  );
}

function ReviewQueue() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState<BarFilters>({});
  const [page, setPage] = useState(0);
  const queue = useOverdueReviewQueue({ ...filters, page: page + 1 });
  const total = useKnownTotal(queue.data?.count);

  const columns: Column<OverdueCase>[] = [
    { key: "task", header: "Task", render: (c) => <><strong>{c.task_reference}</strong> {c.task_title}</> },
    { key: "employee", header: "Employee", render: (c) => c.employee.full_name },
    { key: "department", header: "Department", render: (c) => c.department.code },
    { key: "priority", header: "Priority", render: (c) => PRIORITY_LABEL[c.priority] },
    { key: "deadline", header: "Deadline", render: (c) => <DateTimeText value={c.sla_due_at} /> },
    { key: "overdue", header: "Overdue for", render: (c) => formatOverdueMinutes(c.overdue_minutes) },
    { key: "reason", header: "Employee reason", render: (c) => (c.reason_category ? OVERDUE_CAUSE_LABEL[c.reason_category] : "—") },
    { key: "submitted", header: "Submitted", render: (c) => <DateTimeText value={c.submitted_at} /> },
    { key: "status", header: "Status", render: (c) => <OverdueStatusChip status={c.status} /> },
    { key: "open", header: "", render: (c) => <Button size="small" onClick={() => navigate(`/overdue-cases/${c.id}`)}>Open</Button> },
  ];

  return (
    <Stack spacing={2}>
      <OverdueFilterBar fields={["department", "priority", "reason_category", "date_from", "date_to"]}
        value={filters} onChange={(next) => { setPage(0); setFilters(next); }} />
      {queue.isError && (
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
          <Box sx={{ flex: 1 }}><ApiErrorAlert error={queue.error} /></Box>
          <Button onClick={() => void queue.refetch()}>Retry</Button>
        </Stack>
      )}
      <DataTable caption="Overdue review queue" columns={columns} rows={queue.data?.results ?? []} getRowId={(c) => c.id}
        loading={queue.isPending || queue.isFetching} total={total} page={page} onPageChange={setPage}
        emptyMessage={queue.isError ? "Could not load the review queue."
          : hasActiveFilters(filters) ? "No overdue cases match the selected filters." : "No overdue cases are waiting for your review."} />
    </Stack>
  );
}

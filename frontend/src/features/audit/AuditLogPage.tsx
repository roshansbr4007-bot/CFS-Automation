import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { auditApi } from "../../api/endpoints";
import type { AuditEntry } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText } from "../../components/DateTimeText";
import { JsonDiff } from "../../components/JsonDiff";

export function AuditLogPage() {
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState({ action: "", entity_type: "", from: "", to: "" });
  const [selected, setSelected] = useState<AuditEntry | null>(null);

  const params = { page: page + 1, ...filters };
  const { data, isFetching, error } = useQuery({
    queryKey: ["audit", params],
    queryFn: () => auditApi.list(params),
    placeholderData: keepPreviousData,
  });

  const setFilter = (key: keyof typeof filters) => (value: string) => {
    setFilters((current) => ({ ...current, [key]: value }));
    setPage(0);
  };

  const columns: Column<AuditEntry>[] = [
    { key: "time", header: "Time (IST)", render: (e) => <DateTimeText value={e.occurred_at} /> },
    { key: "actor", header: "By", render: (e) => e.actor_email ?? "System" },
    { key: "action", header: "Action", render: (e) => e.action },
    { key: "entity", header: "Record", render: (e) => `${e.entity_type} ${e.entity_id}` },
    {
      key: "details",
      header: "",
      render: (e) => (
        <Button size="small" onClick={() => setSelected(e)}>
          Details
        </Button>
      ),
    },
  ];

  return (
    <Stack spacing={3}>
      <Typography variant="h2" component="h1">
        Audit log
      </Typography>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
        <TextField label="Action" placeholder="e.g. user.role_changed" value={filters.action}
          onChange={(e) => setFilter("action")(e.target.value)} />
        <TextField label="Record type" placeholder="e.g. user" value={filters.entity_type}
          onChange={(e) => setFilter("entity_type")(e.target.value)} />
        <TextField label="From" type="date" value={filters.from} InputLabelProps={{ shrink: true }}
          onChange={(e) => setFilter("from")(e.target.value)} />
        <TextField label="To" type="date" value={filters.to} InputLabelProps={{ shrink: true }}
          onChange={(e) => setFilter("to")(e.target.value)} />
      </Stack>
      <ApiErrorAlert error={error} />
      <DataTable
        caption="Audit log"
        columns={columns}
        rows={data?.results ?? []}
        getRowId={(e) => e.id}
        loading={isFetching}
        total={data?.count ?? 0}
        page={page}
        onPageChange={setPage}
        emptyMessage="No audit entries match these filters."
      />
      <Dialog open={!!selected} onClose={() => setSelected(null)} fullWidth maxWidth="md">
        <DialogTitle>{selected?.action}</DialogTitle>
        <DialogContent>
          {selected && (
            <Stack spacing={2}>
              <Typography variant="body2" color="text.secondary">
                <DateTimeText value={selected.occurred_at} />, by {selected.actor_email ?? "the system"}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                IP address {selected.ip ?? "not recorded"}. Request {selected.request_id ?? "not recorded"}.
              </Typography>
              <JsonDiff before={selected.old_value} after={selected.new_value} />
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelected(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}

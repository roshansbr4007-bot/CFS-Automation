import { Box, Button, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../../api/apiClient";
import { departmentsApi } from "../../api/endpoints";
import type { Employee, KraListFilters, KraListRow, Paginated } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { downloadKraExport, useKraMonths, type ExportFormat } from "./api";
import { MONTH_NAMES, STATUS_LABEL, monthName, points } from "./labels";

type Filters = Omit<KraListFilters, "page" | "page_size">;
const ANY = "";

function useBandNames() {
  return useQuery({
    queryKey: ["performance", "band-names"],
    queryFn: async () => {
      const schemes = await api<{ bands: { name: string }[] }[]>("/performance/band-schemes/");
      return Array.from(new Set(schemes.flatMap((s) => s.bands.map((b) => b.name)))).sort();
    },
  });
}

/** KRA months for HR / Admin (stored values, backend order). Read-only; exports contain every
 * month matching the filters, never just the visible page. */
export function KraMonthsPage() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState<Filters>({});
  const [page, setPage] = useState(0);
  const months = useKraMonths({ ...filters, page: page + 1 });
  const { data: departments = [] } = useQuery({ queryKey: ["departments"], queryFn: departmentsApi.list });
  const { data: employees } = useQuery({
    queryKey: ["employees", { page_size: 200 }],
    queryFn: () => api<Paginated<Employee>>("/employees/", { query: { page_size: 200 } }),
  });
  const { data: bands = [] } = useBandNames();
  const exporter = useMutation({
    mutationFn: ({ format, current }: { format: ExportFormat; current: Filters }) => downloadKraExport(format, current),
  });
  const set = (key: keyof Filters, raw: string) => {
    const value = raw === ANY ? undefined : ["year", "month", "employee", "department"].includes(key) ? Number(raw) : raw;
    setPage(0);
    exporter.reset();
    setFilters((current) => ({ ...current, [key]: value }));
  };

  const columns: Column<KraListRow>[] = [
    { key: "employee", header: "Employee", render: (r) => r.employee.full_name },
    { key: "department", header: "Department (at calculation)", render: (r) => r.department?.code ?? "—" },
    { key: "month", header: "Month", render: (r) => `${monthName(r.month)} ${r.year}` },
    { key: "status", header: "Status", render: (r) => `${STATUS_LABEL[r.status] ?? r.status}${r.provisional ? " (provisional)" : ""}` },
    { key: "final", header: "Final / applicable", render: (r) => `${points(r.final_total)} / ${points(r.max_points_applicable)}` },
    { key: "band", header: "Band", render: (r) => r.band || "—" },
    { key: "open", header: "", render: (r) => <Button size="small" onClick={() => navigate(`/performance/months/${r.id}`)}>Open</Button> },
  ];

  const field = (key: keyof Filters, label: string, options: [string, string][], width = 170) => (
    <TextField select size="small" label={label} sx={{ width }} value={filters[key] === undefined ? ANY : String(filters[key])}
      onChange={(event) => set(key, event.target.value)}>
      <MenuItem value={ANY}>Any</MenuItem>
      {options.map(([value, text]) => <MenuItem key={value} value={value}>{text}</MenuItem>)}
    </TextField>
  );
  const thisYear = new Date().getFullYear();
  const years = Array.from({ length: 6 }, (_, i) => String(thisYear + 1 - i));

  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h2" component="h1">KRA performance</Typography>
        <Typography color="text.secondary">Stored KRA months (0–10 points). The department is the one recorded when the month was calculated.</Typography>
      </Box>
      <Stack direction="row" spacing={1} component="section" aria-label="Filters" sx={{ flexWrap: "wrap", rowGap: 1 }}>
        {field("year", "Year", years.map((y) => [y, y]), 120)}
        {field("month", "Month", MONTH_NAMES.map((name, i) => [String(i + 1), name]), 150)}
        {field("department", "Department", departments.map((d) => [String(d.id), d.code]), 150)}
        {field("employee", "Employee", (employees?.results ?? []).map((e) => [String(e.id), e.full_name]), 220)}
        {field("status", "Status", Object.entries(STATUS_LABEL), 160)}
        {field("band", "Band", bands.map((b) => [b, b]), 200)}
        {field("provisional", "Provisional", [["true", "Provisional"], ["false", "Month closed"]], 160)}
      </Stack>
      <Stack direction="row" component="section" aria-label="Export" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap" }}>
        <Button variant="outlined" disabled={exporter.isPending} onClick={() => exporter.mutate({ format: "csv", current: filters })}>Export CSV</Button>
        <Button variant="outlined" disabled={exporter.isPending} onClick={() => exporter.mutate({ format: "excel", current: filters })}>Export Excel</Button>
        <Typography variant="body2" color="text.secondary">
          {exporter.isPending ? "Preparing the export…"
            : exporter.isSuccess ? `Downloaded ${exporter.data}.`
              : "Exports include every month matching the filters above. The Excel file adds KPI details and the deduction records."}
        </Typography>
      </Stack>
      {exporter.isError && <ApiErrorAlert error={exporter.error} />}
      {months.isError && <ApiErrorAlert error={months.error} />}
      <DataTable caption="KRA months" columns={columns} rows={months.data?.results ?? []} getRowId={(r) => r.id}
        loading={months.isPending || months.isFetching} total={months.data?.count ?? 0} page={page}
        onPageChange={setPage} emptyMessage={months.isError ? "Could not load the KRA months." : "No KRA months match the filters."} />
    </Stack>
  );
}

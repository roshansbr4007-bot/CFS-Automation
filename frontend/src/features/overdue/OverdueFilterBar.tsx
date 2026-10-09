import { Button, MenuItem, Stack, TextField } from "@mui/material";
import { useQuery } from "@tanstack/react-query";

import { departmentsApi } from "../../api/endpoints";
import { OVERDUE_CAUSES, TASK_PRIORITIES, type OverdueCaseFilters, type OverdueStatus } from "../../api/types";
import { PRIORITY_LABEL } from "../tasks/labels";
import { OVERDUE_CAUSE_LABEL, OVERDUE_STATUS_LABEL } from "./labels";

/** Only filters the backend supports (apps/overdue/filters.py). */
export type FilterField = "status" | "reason_category" | "cause" | "department" | "priority" | "date_from" | "date_to";
export type BarFilters = Pick<OverdueCaseFilters, FilterField>;

const STATUSES: OverdueStatus[] = ["OPEN", "REASON_SUBMITTED", "REVIEWED"];
const DEFAULT_LABELS: Record<FilterField, string> = {
  status: "Status", reason_category: "Employee reason", cause: "Reviewer's cause", department: "Department",
  priority: "Priority", date_from: "Opened from", date_to: "Opened to",
};

export function hasActiveFilters(filters: BarFilters): boolean {
  return Object.values(filters).some((v) => v !== undefined && v !== "");
}

/** A resettable filter bar. Changing any value replaces the filter object (callers reset the page). */
export function OverdueFilterBar({ fields, value, onChange, labels = {} }: {
  fields: FilterField[]; value: BarFilters; onChange: (next: BarFilters) => void; labels?: Partial<Record<FilterField, string>>;
}) {
  const label = (f: FilterField) => labels[f] ?? DEFAULT_LABELS[f];
  const { data: departments = [] } = useQuery({
    queryKey: ["departments"], queryFn: departmentsApi.list, enabled: fields.includes("department"),
  });
  const set = <K extends FilterField>(key: K, raw: string | number) =>
    onChange({ ...value, [key]: raw === "" ? undefined : raw });
  const select = (field: FilterField, all: string, options: [string | number, string][], minWidth: number) => (
    <TextField key={field} select size="small" label={label(field)} value={value[field] ?? ""} sx={{ minWidth }}
      onChange={(e) => set(field, field === "department" && e.target.value !== "" ? Number(e.target.value) : e.target.value)}>
      <MenuItem value="">{all}</MenuItem>
      {options.map(([v, text]) => <MenuItem key={v} value={v}>{text}</MenuItem>)}
    </TextField>
  );
  const date = (field: "date_from" | "date_to") => (
    <TextField key={field} size="small" type="date" label={label(field)} value={value[field] ?? ""}
      onChange={(e) => set(field, e.target.value)} InputLabelProps={{ shrink: true }} />
  );
  const controls: Record<FilterField, () => JSX.Element> = {
    status: () => select("status", "All statuses", STATUSES.map((s) => [s, OVERDUE_STATUS_LABEL[s]]), 170),
    reason_category: () => select("reason_category", "All reasons", OVERDUE_CAUSES.map((c) => [c, OVERDUE_CAUSE_LABEL[c]]), 170),
    cause: () => select("cause", "All causes", OVERDUE_CAUSES.map((c) => [c, OVERDUE_CAUSE_LABEL[c]]), 170),
    department: () => select("department", "All departments", departments.map((d) => [d.id, d.code]), 160),
    priority: () => select("priority", "All priorities", TASK_PRIORITIES.map((p) => [p, PRIORITY_LABEL[p]]), 140),
    date_from: () => date("date_from"),
    date_to: () => date("date_to"),
  };
  return (
    <Stack direction="row" component="section" aria-label="Filters" sx={{ flexWrap: "wrap", gap: 1.5, alignItems: "center" }}>
      {fields.map((f) => controls[f]())}
      <Button onClick={() => onChange({})} disabled={!hasActiveFilters(value)}>Clear filters</Button>
    </Stack>
  );
}

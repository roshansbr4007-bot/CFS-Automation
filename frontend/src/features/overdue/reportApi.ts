/** Phase 9 Stage 5.3: the existing overdue report contract (apps/overdue/api/views.py).
 * - GET /overdue-cases/reports/               paginated cases the caller may see, report order
 * - GET /overdue-cases/reports/export/csv/    attachment, HR / Admin only (403 otherwise)
 * - GET /overdue-cases/reports/export/excel/  attachment, HR / Admin only (403 otherwise)
 * The backend computes everything; React only lists rows and downloads files. */
import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "../../api/apiClient";
import type { OverdueCase, OverdueCaseFilters, Paginated } from "../../api/types";

export const reportKey = (filters: OverdueCaseFilters) => ["overdue-cases", "list", "report", filters] as const;

export function fetchOverdueReport(filters: OverdueCaseFilters = {}) {
  return api<Paginated<OverdueCase>>("/overdue-cases/reports/", { query: { ...filters } });
}

/** Under ["overdue-cases", "list"], so the Stage 5.1 submit / review mutations refresh it too. */
export function useOverdueReport(filters: OverdueCaseFilters, enabled = true) {
  return useQuery({ queryKey: reportKey(filters), queryFn: () => fetchOverdueReport(filters), enabled });
}

export type ReportFormat = "csv" | "excel";
const FALLBACK_NAME: Record<ReportFormat, string> = { csv: "overdue-cases.csv", excel: "overdue-cases.xlsx" };

/** Downloads an export with the CURRENT filters (pagination excluded: an export is the whole
 * filtered set). The JSON api() helper cannot carry a file, hence this small fetch; errors are
 * the project's ApiError so pages show them like any other API error. Returns the file name. */
export async function downloadOverdueReport(format: ReportFormat, filters: OverdueCaseFilters): Promise<string> {
  const url = new URL(`/api/v1/overdue-cases/reports/export/${format}/`, window.location.origin);
  for (const [key, value] of Object.entries(filters)) {
    if (key === "page" || key === "page_size") continue;
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new ApiError(response.status, data?.code ?? "error", data?.message ?? "The export could not be created.", data?.fields ?? {});
  }
  const blob = await response.blob();
  const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "")?.[1] ?? FALLBACK_NAME[format];
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(href);
  return name;
}

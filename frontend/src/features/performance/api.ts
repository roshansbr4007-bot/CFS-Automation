/** Phase 7.4 KRA performance: the backend computes everything; React only lists and downloads.
 * - GET /performance/my/kra-months/          my KRA months (404 "no_employee_record" without one)
 * - GET /performance/my/kra-months/{id}/     one of my months (numbers only when shown)
 * - GET /performance/my/annual/?year=        my approved annual figures
 * - GET /performance/reports/                the EXISTING legacy report (unchanged; Home asks
 *                                            for my own FINALIZED months only)
 * - GET /performance/months/                 KRA months, HR / Admin (403 otherwise)
 * - GET /performance/months/{id}/            one KRA month, HR / Admin
 * - GET /performance/months/export/{csv|excel}/  attachment, HR / Admin */
import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "../../api/apiClient";
import type {
  KraListFilters, KraListRow, KraMyAnnual, KraMyHistory, KraMyMonth, KraReviewMonth,
  LegacyPerformanceRow, Paginated,
} from "../../api/types";

export const performanceKeys = {
  myHistory: ["performance", "my", "history"] as const,
  myMonth: (id: number) => ["performance", "my", "month", id] as const,
  myAnnual: (year: number) => ["performance", "my", "annual", year] as const,
  myLegacy: (employee: number) => ["performance", "my", "legacy", employee] as const,
  list: (filters: KraListFilters) => ["performance", "months", "list", filters] as const,
  detail: (id: number) => ["performance", "months", "detail", id] as const,
};

/** null when this login has no employee record (the backend answers 404 "no_employee_record"). */
export async function fetchMyHistory(): Promise<KraMyHistory | null> {
  try {
    return await api<KraMyHistory>("/performance/my/kra-months/");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export function useMyHistory() {
  return useQuery({ queryKey: performanceKeys.myHistory, queryFn: fetchMyHistory });
}

export function useMyMonth(id: number | null) {
  return useQuery({
    queryKey: performanceKeys.myMonth(id ?? 0),
    queryFn: () => api<KraMyMonth>(`/performance/my/kra-months/${id}/`),
    enabled: id !== null,
  });
}

export function useMyAnnual(year: number, enabled: boolean) {
  return useQuery({
    queryKey: performanceKeys.myAnnual(year),
    queryFn: () => api<KraMyAnnual>("/performance/my/annual/", { query: { year } }),
    enabled,
  });
}

/** My FINALIZED legacy (0-100) months from the existing legacy report, every page. */
export async function fetchMyLegacy(employee: number): Promise<LegacyPerformanceRow[]> {
  const rows: LegacyPerformanceRow[] = [];
  for (let page = 1; ; page += 1) {
    const body = await api<Paginated<LegacyPerformanceRow>>("/performance/reports/", {
      query: { employee, status: "FINALIZED", page_size: 200, page },
    });
    rows.push(...body.results);
    if (!body.next) return rows;
  }
}

export function useMyLegacy(employee: number | null) {
  return useQuery({
    queryKey: performanceKeys.myLegacy(employee ?? 0),
    queryFn: () => fetchMyLegacy(employee as number),
    enabled: employee !== null,
  });
}

export function useKraMonths(filters: KraListFilters) {
  return useQuery({
    queryKey: performanceKeys.list(filters),
    queryFn: () => api<Paginated<KraListRow>>("/performance/months/", { query: { ...filters } }),
  });
}

export function useKraMonth(id: number) {
  return useQuery({
    queryKey: performanceKeys.detail(id),
    queryFn: () => api<KraReviewMonth>(`/performance/months/${id}/`),
    enabled: Number.isInteger(id) && id > 0,
  });
}

export type ExportFormat = "csv" | "excel";
const FALLBACK_NAME: Record<ExportFormat, string> = { csv: "kra-performance.csv", excel: "kra-performance.xlsx" };

/** Downloads an export with the CURRENT filters (pagination excluded: an export is the whole
 * filtered set). Same approach as the overdue export (features/overdue/reportApi.ts). */
export async function downloadKraExport(format: ExportFormat, filters: KraListFilters): Promise<string> {
  const url = new URL(`/api/v1/performance/months/export/${format}/`, window.location.origin);
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

/** Phase 7.5A KRA configuration screens: the existing Phase 7.1 configuration API plus the
 * read-only readiness check. The backend decides everything (validation, locking, activation);
 * these calls only send what HR / Admin entered and show what the server answers.
 *
 * Who (enforced by the backend): reading = configure_kpis (HR) or approve_kpi_config (Admin);
 * drafts, defaults, overrides, KPI descriptions = configure_kpis; activate / retire =
 * approve_kpi_config. ACTIVE and RETIRED versions are never edited (409 configuration_locked). */
import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";

import { api, ApiError } from "../../api/apiClient";
import type {
  KpiCfgBandScheme, KpiCfgDeductionRule, KpiCfgKpi, KpiCfgLine, KpiCfgComponent, KpiCfgOverride,
  KpiCfgPlan, KpiCfgPlanDefault, KpiCfgPlanDetail, KpiCfgReadiness, KpiCfgResolution,
  KpiCfgScoringRule,
} from "../../api/types";

type Body = Record<string, unknown>;
const P = "/performance";

export const cfgKeys = {
  all: ["kraconfig"] as const,
  kpis: ["kraconfig", "kpis"] as const,
  plans: ["kraconfig", "plans"] as const,
  plan: (id: number) => ["kraconfig", "plan", id] as const,
  readiness: (id: number) => ["kraconfig", "readiness", id] as const,
  scoringRules: ["kraconfig", "scoring-rules"] as const,
  scoringRule: (id: number) => ["kraconfig", "scoring-rule", id] as const,
  bandSchemes: ["kraconfig", "band-schemes"] as const,
  bandScheme: (id: number) => ["kraconfig", "band-scheme", id] as const,
  defaults: ["kraconfig", "plan-defaults"] as const,
  overrides: ["kraconfig", "overrides"] as const,
  resolution: (employee: number, date: string) => ["kraconfig", "resolution", employee, date] as const,
};

export const cfgApi = {
  kpis: () => api<KpiCfgKpi[]>(`${P}/kpis/`),
  updateKpi: (id: number, body: Body) => api<KpiCfgKpi>(`${P}/kpis/${id}/`, { method: "PATCH", body }),

  plans: () => api<KpiCfgPlan[]>(`${P}/plans/`),
  plan: (id: number) => api<KpiCfgPlanDetail>(`${P}/plans/${id}/`),
  readiness: (id: number) => api<KpiCfgReadiness>(`${P}/plans/${id}/readiness/`),
  createPlan: (body: Body) => api<KpiCfgPlanDetail>(`${P}/plans/`, { method: "POST", body }),
  updatePlan: (id: number, body: Body) => api<KpiCfgPlanDetail>(`${P}/plans/${id}/`, { method: "PATCH", body }),
  deletePlan: (id: number) => api<void>(`${P}/plans/${id}/`, { method: "DELETE" }),
  clonePlan: (id: number) => api<KpiCfgPlanDetail>(`${P}/plans/${id}/clone/`, { method: "POST" }),
  activatePlan: (id: number) => api<KpiCfgPlanDetail>(`${P}/plans/${id}/activate/`, { method: "POST" }),
  retirePlan: (id: number, body: Body) => api<KpiCfgPlanDetail>(`${P}/plans/${id}/retire/`, { method: "POST", body }),

  createLine: (plan: number, body: Body) => api<KpiCfgLine>(`${P}/plans/${plan}/lines/`, { method: "POST", body }),
  updateLine: (id: number, body: Body) => api<KpiCfgLine>(`${P}/plan-lines/${id}/`, { method: "PATCH", body }),
  deleteLine: (id: number) => api<void>(`${P}/plan-lines/${id}/`, { method: "DELETE" }),
  createComponent: (line: number, body: Body) =>
    api<KpiCfgComponent>(`${P}/plan-lines/${line}/components/`, { method: "POST", body }),
  updateComponent: (id: number, body: Body) => api<KpiCfgComponent>(`${P}/components/${id}/`, { method: "PATCH", body }),
  deleteComponent: (id: number) => api<void>(`${P}/components/${id}/`, { method: "DELETE" }),
  createDeduction: (plan: number, body: Body) =>
    api<KpiCfgDeductionRule>(`${P}/plans/${plan}/deduction-rules/`, { method: "POST", body }),
  updateDeduction: (id: number, body: Body) =>
    api<KpiCfgDeductionRule>(`${P}/deduction-rules/${id}/`, { method: "PATCH", body }),
  deleteDeduction: (id: number) => api<void>(`${P}/deduction-rules/${id}/`, { method: "DELETE" }),

  scoringRules: () => api<KpiCfgScoringRule[]>(`${P}/scoring-rules/`),
  scoringRule: (id: number) => api<KpiCfgScoringRule>(`${P}/scoring-rules/${id}/`),
  createScoringRule: (body: Body) => api<KpiCfgScoringRule>(`${P}/scoring-rules/`, { method: "POST", body }),
  updateScoringRule: (id: number, body: Body) =>
    api<KpiCfgScoringRule>(`${P}/scoring-rules/${id}/`, { method: "PATCH", body }),
  cloneScoringRule: (id: number) => api<KpiCfgScoringRule>(`${P}/scoring-rules/${id}/clone/`, { method: "POST" }),
  activateScoringRule: (id: number) =>
    api<KpiCfgScoringRule>(`${P}/scoring-rules/${id}/activate/`, { method: "POST" }),
  retireScoringRule: (id: number, body: Body) =>
    api<KpiCfgScoringRule>(`${P}/scoring-rules/${id}/retire/`, { method: "POST", body }),

  bandSchemes: () => api<KpiCfgBandScheme[]>(`${P}/band-schemes/`),
  bandScheme: (id: number) => api<KpiCfgBandScheme>(`${P}/band-schemes/${id}/`),
  createBandScheme: (body: Body) => api<KpiCfgBandScheme>(`${P}/band-schemes/`, { method: "POST", body }),
  updateBandScheme: (id: number, body: Body) =>
    api<KpiCfgBandScheme>(`${P}/band-schemes/${id}/`, { method: "PATCH", body }),
  cloneBandScheme: (id: number) => api<KpiCfgBandScheme>(`${P}/band-schemes/${id}/clone/`, { method: "POST" }),
  activateBandScheme: (id: number) =>
    api<KpiCfgBandScheme>(`${P}/band-schemes/${id}/activate/`, { method: "POST" }),
  retireBandScheme: (id: number, body: Body) =>
    api<KpiCfgBandScheme>(`${P}/band-schemes/${id}/retire/`, { method: "POST", body }),

  defaults: () => api<KpiCfgPlanDefault[]>(`${P}/plan-defaults/`),
  createDefault: (body: Body) => api<KpiCfgPlanDefault>(`${P}/plan-defaults/`, { method: "POST", body }),
  endDefault: (id: number, body: Body) =>
    api<KpiCfgPlanDefault>(`${P}/plan-defaults/${id}/end/`, { method: "POST", body }),
  overrides: () => api<KpiCfgOverride[]>(`${P}/assignments/`),
  createOverride: (body: Body) => api<KpiCfgOverride>(`${P}/assignments/`, { method: "POST", body }),
  endOverride: (id: number, body: Body) =>
    api<KpiCfgOverride>(`${P}/assignments/${id}/end/`, { method: "POST", body }),
  resolve: (employee: number, date: string) =>
    api<KpiCfgResolution>(`${P}/plan-resolution/`, { query: { employee, date } }),
};

/** A 4xx (for example 404 for a deleted or unknown version) is final: no retries. */
const retry = (count: number, error: Error) => !(error instanceof ApiError && error.status < 500) && count < 3;

/** The backend's field errors as flat "field" / "steps.1.score_pct" keys -> one message each.
 * The configuration services answer with flat keys; DRF's nested serializers (steps, bands)
 * answer with lists of objects, which are flattened here so they reach the right input. */
export function cfgErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {};
  const out: Record<string, string> = {};
  const walk = (value: unknown, path: string) => {
    if (Array.isArray(value)) {
      if (value.every((item) => typeof item === "string")) {
        if (value.length) out[path] = value.join(" ");
        return;
      }
      value.forEach((item, index) => walk(item, path ? `${path}.${index}` : String(index)));
    } else if (value && typeof value === "object") {
      for (const [key, inner] of Object.entries(value)) walk(inner, path ? `${path}.${key}` : key);
    } else if (typeof value === "string") {
      out[path] = value;
    }
  };
  walk(error.fields, "");
  return out;
}

/** `enabled = false` while a dialog that needs the list is closed (nothing is fetched). */
export const usePlans = (enabled = true) => useQuery({ queryKey: cfgKeys.plans, queryFn: cfgApi.plans, enabled });
export const useKpis = (enabled = true) => useQuery({ queryKey: cfgKeys.kpis, queryFn: cfgApi.kpis, enabled });
export const useScoringRules = (enabled = true) =>
  useQuery({ queryKey: cfgKeys.scoringRules, queryFn: cfgApi.scoringRules, enabled });
export const useBandSchemes = (enabled = true) =>
  useQuery({ queryKey: cfgKeys.bandSchemes, queryFn: cfgApi.bandSchemes, enabled });

const validId = (id: number) => Number.isInteger(id) && id > 0;

export function usePlan(id: number) {
  return useQuery({ queryKey: cfgKeys.plan(id), queryFn: () => cfgApi.plan(id), enabled: validId(id), retry });
}

export function useReadiness(id: number, enabled = true) {
  return useQuery({ queryKey: cfgKeys.readiness(id), queryFn: () => cfgApi.readiness(id), enabled: enabled && validId(id), retry });
}

export function useScoringRule(id: number) {
  return useQuery({ queryKey: cfgKeys.scoringRule(id), queryFn: () => cfgApi.scoringRule(id), enabled: validId(id), retry });
}

export function useBandScheme(id: number | null | undefined) {
  return useQuery({
    queryKey: cfgKeys.bandScheme(id ?? 0), queryFn: () => cfgApi.bandScheme(id as number), enabled: !!id && validId(id),
    retry,
  });
}

/** Any configuration change: right after it, EVERYTHING configuration-related is reloaded from the
 * server (lists, details and readiness); `onDone` (close / navigate) runs first, so the previous
 * data may show for a moment until the reload lands - the server re-checks every action anyway.
 * A failed change (400 / 409) also reloads, because someone else may have changed it meanwhile.
 * `removeKeys`: queries of something just deleted, dropped (not reloaded) before `onDone`. */
export function useCfgMutation<TArgs = void, TResult = unknown>(
  fn: (args: TArgs) => Promise<TResult>,
  onDone?: (result: TResult) => void,
  removeKeys: QueryKey[] = [],
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: async (result) => {
      for (const queryKey of removeKeys) qc.removeQueries({ queryKey, exact: true });
      onDone?.(result);
      await qc.invalidateQueries({ queryKey: cfgKeys.all });
    },
    onError: () => qc.invalidateQueries({ queryKey: cfgKeys.all }),
  });
}

/** Only the entries of `next` that differ from `current` (a PATCH then never resends, and so
 * never re-validates or overwrites, a stored value the user did not touch). */
export function changed(current: Record<string, unknown>, next: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(next).filter(([key, value]) => JSON.stringify(current[key]) !== JSON.stringify(value)));
}

/** Phase 9 overdue cases: React Query keys and hooks (foundation for the later screens).
 * Same conventions as the rest of the app: plain array keys, filters/ids inside the key,
 * narrow invalidation after a mutation, the backend as the only source of truth. */
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { ApiError } from "../../api/apiClient";
import { employeesApi, overdueCasesApi } from "../../api/endpoints";
import type { Employee, OverdueCase, OverdueCaseFilters, OverdueReasonInput, OverdueReviewInput, Paginated } from "../../api/types";

export const overdueKeys = {
  all: ["overdue-cases"] as const,
  lists: () => ["overdue-cases", "list"] as const,
  mine: (filters: OverdueCaseFilters) => ["overdue-cases", "list", "mine", filters] as const,
  reviewQueue: (filters: OverdueCaseFilters) => ["overdue-cases", "list", "review-queue", filters] as const,
  detail: (id: number) => ["overdue-cases", "detail", id] as const,
};

/** The signed-in user's own employee record; shared cache key for any later screen. */
export const OWN_EMPLOYEE_KEY = ["employees", "me"] as const;

/** null when this login has no employee record (the backend answers 404 "no_employee_record"). */
export async function fetchOwnEmployee(): Promise<Employee | null> {
  try {
    return await employeesApi.me();
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

const EMPTY_PAGE: Paginated<OverdueCase> = { count: 0, next: null, previous: null, results: [] };

/** My own overdue cases (I am the employee recorded on them), whatever my role. Without an
 * employee record nobody can have cases, so the result is an empty page (no list request). */
export function useMyOverdueCases(filters: OverdueCaseFilters = {}) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: overdueKeys.mine(filters),
    queryFn: async () => {
      const me = await qc.fetchQuery({ queryKey: OWN_EMPLOYEE_KEY, queryFn: fetchOwnEmployee, staleTime: Infinity });
      return me ? overdueCasesApi.list({ ...filters, employee: me.id }) : EMPTY_PAGE;
    },
  });
}

/** Cases waiting for my review (backend-scoped; never my own). */
export function useOverdueReviewQueue(filters: OverdueCaseFilters = {}) {
  return useQuery({ queryKey: overdueKeys.reviewQueue(filters), queryFn: () => overdueCasesApi.reviewQueue(filters) });
}

export function useOverdueCase(id: number) {
  return useQuery({
    queryKey: overdueKeys.detail(id),
    queryFn: () => overdueCasesApi.get(id),
    enabled: Number.isInteger(id) && id > 0,
  });
}

function afterChange(qc: QueryClient, updated: OverdueCase) {
  qc.setQueryData(overdueKeys.detail(updated.id), updated); // the server's answer, not a guess
  return qc.invalidateQueries({ queryKey: overdueKeys.lists() }); // my cases and review queues only
}

export function useSubmitOverdueReason(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: OverdueReasonInput) => overdueCasesApi.submit(id, input),
    onSuccess: (updated) => afterChange(qc, updated),
  });
}

export function useReviewOverdueCase(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: OverdueReviewInput) => overdueCasesApi.review(id, input),
    onSuccess: (updated) => afterChange(qc, updated),
  });
}

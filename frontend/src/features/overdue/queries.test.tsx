import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/apiClient";
import { overdueCasesApi } from "../../api/endpoints";
import { OVERDUE_CAUSES, type OverdueCase } from "../../api/types";
import { server } from "../../test/server";
import {
  OWN_EMPLOYEE_KEY, overdueKeys, useMyOverdueCases, useOverdueCase, useOverdueReviewQueue,
  useReviewOverdueCase, useSubmitOverdueReason,
} from "./queries";

const CASE: OverdueCase = {
  id: 5, status: "OPEN", opened_at: "2026-10-05T05:30:00Z", opened_via: "TICK",
  task_id: 9, task_reference: "T-000009", task_title: "Map RM codes",
  employee: { id: 42, full_name: "Rahul Verma", employee_code: "CFS-042" },
  department: { id: 1, code: "OPS", name: "Operations" }, task_creator: { id: 3, email: "ops@example.com" },
  task_assigned_at: "2026-10-05T04:30:00Z", priority: "HIGH", category_name: "Operations",
  sla_start_at: "2026-10-05T04:30:00Z", sla_due_at: "2026-10-05T05:30:00Z", sla_rule_code: "ONE_HOUR",
  sla_rule_name: "One hour", overdue_at: "2026-10-05T05:30:00Z", completed_at: null, overdue_minutes: 12,
  reason_category: "", explanation: "", submitted_at: null, submitted_by: null,
  cause: "", review_remark: "", reviewed_at: null, reviewed_by: null, version: 1,
  can_submit: true, can_review: false,
};
const page = (results: OverdueCase[]) => ({ count: results.length, next: null, previous: null, results });

interface Seen { method: string; url: URL; body: unknown; csrf: string | null }

/** Records every overdue / own-employee request; answers with `reply` (default: a page). */
function recorder(reply: (seen: Seen) => Response = () => HttpResponse.json(page([CASE]))) {
  const seen: Seen[] = [];
  const handle = async ({ request }: { request: Request }) => {
    const entry: Seen = {
      method: request.method, url: new URL(request.url), csrf: request.headers.get("X-CSRFToken"),
      body: request.method === "POST" ? await request.json() : null,
    };
    seen.push(entry);
    return reply(entry);
  };
  server.use(http.all("*/api/v1/overdue-cases/*", handle), http.get("*/api/v1/employees/me/", handle));
  return seen;
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  return { qc, wrapper };
}

const params = (seen: Seen) => Object.fromEntries(seen.url.searchParams.entries());
const paths = (seen: Seen[]) => seen.map((s) => `${s.method} ${s.url.pathname}`);

describe("overdue cases API functions", () => {
  it("sends exactly the backend filters as query parameters", async () => {
    const seen = recorder();
    await overdueCasesApi.list({
      status: "OPEN", employee: 7, department: 2, date_from: "2026-10-01", date_to: "2026-10-31",
      cause: "SYSTEM", reason_category: "DEPENDENCY", task: 9, priority: "HIGH", page: 2, page_size: 50,
    });
    await overdueCasesApi.list();
    expect(paths(seen)).toEqual(["GET /api/v1/overdue-cases/", "GET /api/v1/overdue-cases/"]);
    expect(params(seen[0])).toEqual({
      status: "OPEN", employee: "7", department: "2", date_from: "2026-10-01", date_to: "2026-10-31",
      cause: "SYSTEM", reason_category: "DEPENDENCY", task: "9", priority: "HIGH", page: "2", page_size: "50",
    });
    expect(params(seen[1])).toEqual({}); // nothing invented
  });

  it("asks for the review queue with reviewable=1", async () => {
    const seen = recorder();
    await overdueCasesApi.reviewQueue({ department: 2 });
    expect(params(seen[0])).toEqual({ department: "2", reviewable: "1" });
  });

  it("uses the right method, path and JSON body (with CSRF) for detail, submit and review", async () => {
    const seen = recorder(() => HttpResponse.json(CASE));
    await overdueCasesApi.get(5);
    await overdueCasesApi.submit(5, { version: 1, reason_category: "DEPENDENCY", explanation: "Waiting for RTA" });
    await overdueCasesApi.review(5, { version: 2, cause: "SYSTEM", remark: "Portal outage" });
    expect(paths(seen)).toEqual([
      "GET /api/v1/overdue-cases/5/", "POST /api/v1/overdue-cases/5/submit/", "POST /api/v1/overdue-cases/5/review/",
    ]);
    expect(seen[1].body).toEqual({ version: 1, reason_category: "DEPENDENCY", explanation: "Waiting for RTA" });
    expect(seen[2].body).toEqual({ version: 2, cause: "SYSTEM", remark: "Portal outage" });
    expect(seen[1].csrf).not.toBeNull();
    expect(seen[2].csrf).not.toBeNull();
  });

  it("surfaces backend errors as ApiError with code and fields", async () => {
    recorder(() => HttpResponse.json(
      { code: "overdue_case_state", message: "A reason has already been submitted for this case.", fields: {} },
      { status: 409 },
    ));
    const failure = overdueCasesApi.submit(5, { version: 1, reason_category: "OTHER", explanation: "x" });
    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({ status: 409, code: "overdue_case_state" });
  });
});

describe("overdue case query hooks", () => {
  it("my cases: resolves my employee once, then filters by it", async () => {
    const seen = recorder((s) => (s.url.pathname.endsWith("/employees/me/")
      ? HttpResponse.json({ id: 42, full_name: "Rahul Verma" }) : HttpResponse.json(page([CASE]))));
    const { wrapper, qc } = setup();
    const open = renderHook(() => useMyOverdueCases({ status: "OPEN" }), { wrapper });
    await waitFor(() => expect(open.result.current.isSuccess).toBe(true));
    const all = renderHook(() => useMyOverdueCases(), { wrapper });
    await waitFor(() => expect(all.result.current.isSuccess).toBe(true));
    expect(open.result.current.data?.results).toEqual([CASE]);
    expect(seen.filter((s) => s.url.pathname.endsWith("/employees/me/"))).toHaveLength(1); // cached
    const lists = seen.filter((s) => s.url.pathname === "/api/v1/overdue-cases/").map(params);
    expect(lists).toEqual([{ status: "OPEN", employee: "42" }, { employee: "42" }]);
    expect(qc.getQueryData(OWN_EMPLOYEE_KEY)).toMatchObject({ id: 42 });
  });

  it("my cases without an employee record is an empty page, with no list request", async () => {
    const seen = recorder((s) => (s.url.pathname.endsWith("/employees/me/")
      ? HttpResponse.json({ code: "no_employee_record", message: "No record.", fields: {} }, { status: 404 })
      : HttpResponse.json(page([CASE]))));
    const { wrapper } = setup();
    const { result } = renderHook(() => useMyOverdueCases(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual({ count: 0, next: null, previous: null, results: [] });
    expect(seen.some((s) => s.url.pathname === "/api/v1/overdue-cases/")).toBe(false);
  });

  it("my cases surfaces other errors", async () => {
    recorder(() => HttpResponse.json({ code: "error", message: "Server error.", fields: {} }, { status: 500 }));
    const { wrapper } = setup();
    const { result } = renderHook(() => useMyOverdueCases(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toMatchObject({ status: 500 });
  });

  it("review queue: one cache entry per filter set", async () => {
    const seen = recorder();
    const { wrapper, qc } = setup();
    const a = renderHook(() => useOverdueReviewQueue({ department: 1 }), { wrapper });
    const b = renderHook(() => useOverdueReviewQueue({ department: 2 }), { wrapper });
    await waitFor(() => expect(a.result.current.isSuccess && b.result.current.isSuccess).toBe(true));
    expect(seen.map(params)).toEqual(expect.arrayContaining([
      { department: "1", reviewable: "1" }, { department: "2", reviewable: "1" },
    ]));
    expect(qc.getQueryData(overdueKeys.reviewQueue({ department: 1 }))).toBeDefined();
    expect(qc.getQueryData(overdueKeys.reviewQueue({ department: 2 }))).toBeDefined();
  });

  it("detail fetches by id and does nothing for an invalid id", async () => {
    const seen = recorder(() => HttpResponse.json(CASE));
    const { wrapper } = setup();
    const valid = renderHook(() => useOverdueCase(5), { wrapper });
    await waitFor(() => expect(valid.result.current.data).toEqual(CASE));
    const invalid = renderHook(() => useOverdueCase(Number.NaN), { wrapper });
    expect(invalid.result.current.fetchStatus).toBe("idle");
    expect(paths(seen)).toEqual(["GET /api/v1/overdue-cases/5/"]);
  });
});

describe("overdue case mutations", () => {
  function primed() {
    const { wrapper, qc } = setup();
    qc.setQueryData(overdueKeys.mine({}), page([CASE]));
    qc.setQueryData(overdueKeys.reviewQueue({}), page([]));
    qc.setQueryData(["tasks", {}], page([])); // unrelated data must stay untouched
    return { wrapper, qc };
  }

  it("submit posts the payload, stores the server's case and refreshes only overdue lists", async () => {
    const submitted = { ...CASE, status: "REASON_SUBMITTED", reason_category: "DEPENDENCY", version: 2 } as const;
    const seen = recorder(() => HttpResponse.json(submitted));
    const { wrapper, qc } = primed();
    const { result } = renderHook(() => useSubmitOverdueReason(5), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ version: 1, reason_category: "DEPENDENCY", explanation: "Waiting for RTA" });
    });
    expect(seen[0].body).toEqual({ version: 1, reason_category: "DEPENDENCY", explanation: "Waiting for RTA" });
    expect(qc.getQueryData(overdueKeys.detail(5))).toEqual(submitted);
    expect(qc.getQueryState(overdueKeys.mine({}))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(overdueKeys.reviewQueue({}))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(["tasks", {}])?.isInvalidated).toBe(false);
  });

  it("review posts cause and remark and stores the reviewed case", async () => {
    const reviewed = { ...CASE, status: "REVIEWED", cause: "SYSTEM", review_remark: "Portal outage", version: 3 } as const;
    const seen = recorder(() => HttpResponse.json(reviewed));
    const { wrapper, qc } = primed();
    const { result } = renderHook(() => useReviewOverdueCase(5), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ version: 2, cause: "SYSTEM", remark: "Portal outage" });
    });
    expect(seen.map((s) => `${s.method} ${s.url.pathname}`)).toEqual(["POST /api/v1/overdue-cases/5/review/"]);
    expect(seen[0].body).toEqual({ version: 2, cause: "SYSTEM", remark: "Portal outage" });
    expect(qc.getQueryData(overdueKeys.detail(5))).toEqual(reviewed);
    expect(qc.getQueryState(overdueKeys.reviewQueue({}))?.isInvalidated).toBe(true);
    expect(qc.getQueryState(["tasks", {}])?.isInvalidated).toBe(false);
  });

  it("a failed submit changes no cache", async () => {
    recorder(() => HttpResponse.json({ code: "validation_error", message: "Invalid.", fields: { explanation: ["Required."] } }, { status: 400 }));
    const { wrapper, qc } = primed();
    const { result } = renderHook(() => useSubmitOverdueReason(5), { wrapper });
    await act(async () => {
      await expect(result.current.mutateAsync({ version: 1, reason_category: "OTHER", explanation: "" }))
        .rejects.toMatchObject({ status: 400, fields: { explanation: ["Required."] } });
    });
    expect(qc.getQueryData(overdueKeys.detail(5))).toBeUndefined();
    expect(qc.getQueryState(overdueKeys.mine({}))?.isInvalidated).toBe(false);
  });
});

describe("contract", () => {
  it("uses the backend's cause vocabulary exactly", () => {
    expect(OVERDUE_CAUSES).toEqual(["DEPENDENCY", "EMPLOYEE", "SYSTEM", "CLIENT", "OTHER"]);
  });
});

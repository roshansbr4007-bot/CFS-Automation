import type { DailyActivity, SlaClock, TaskDetail } from "../../api/types";

export function makeTask(overrides: Partial<TaskDetail> = {}): TaskDetail {
  return {
    id: 1, reference: "T-000001", title: "Process SIP mandate", description: "Client K. Mehta",
    task_type: "ADHOC", template: null, category: { id: 3, code: "OPERATIONS", name: "Operations" },
    source: "MANUAL", responsibility: null, schedule: null, occurrence_date: null, generated_at: null,
    trigger_at: null, priority: "HIGH", status: "PENDING",
    sla: { resolution: null, resolution_note: "No SLA configured", acknowledgment: null },
    department: { id: 1, code: "OPS", name: "Operations" },
    created_by: { id: 2, email: "manager@example.com", full_name: "Meera Manager" },
    assigned_to: { id: 10, full_name: "Rahul Sharma" },
    assigned_by: { id: 2, email: "manager@example.com", full_name: "Meera Manager" },
    assigned_at: "2026-10-05T04:30:00Z", received_at: null, received_at_source: null,
    acknowledgment_required: false, acknowledged_at: null, started_at: null, completed_at: null,
    completed_by: null, completion_source: null, verification_required: false,
    verification_status: "NOT_REQUIRED", rework_count: 0, blocked_reason: "", blocked_at: null,
    cancelled_reason: "", cancelled_at: null, version: 1,
    created_at: "2026-10-05T04:30:00Z", updated_at: "2026-10-05T04:30:00Z",
    allowed_actions: ["start", "block", "cancel", "comment", "attach"],
    assignments: [{
      id: 1, from_employee: null, to_employee: { id: 10, full_name: "Rahul Sharma" },
      assigned_by: { id: 2, email: "manager@example.com", full_name: "Meera Manager" },
      assigned_at: "2026-10-05T04:30:00Z", note: "",
    }],
    verifications: [],
    ...overrides,
  };
}

export const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });

export function makeClock(overrides: Partial<SlaClock> = {}): SlaClock {
  return {
    kind: "RESOLUTION", rule_code: "FEED_UPLOAD_2H", rule_name: "Feed upload", rule_version: 1,
    rule_type: "DURATION", clock: "CALENDAR", trigger: "FIXED_TIME", duration_minutes: 120,
    start_at: "2026-10-05T04:30:00Z", due_at: "2026-10-05T06:30:00Z", state: "CRITICAL",
    elapsed_pct: 75, remaining_seconds: 1800, warning_at: "2026-10-05T05:30:00Z",
    critical_at: "2026-10-05T06:00:00Z", overdue_at: null, stopped_at: null, stop_reason: null,
    outcome: null, waiting_for: null, ...overrides,
  };
}

/** Phase 5.1: a generated daily activity as the backend reports it just before 11:00 IST
 * (3630 s left, shown as "1h 00m remaining" with a 30-second margin for slow test machines). */
export function makeActivity(overrides: Partial<DailyActivity> = {}): DailyActivity {
  return {
    task_id: 2, reference: "T-000002", title: "Feed Upload — 05 Oct 2026",
    responsibility: { id: 1, code: "FEED_UPLOAD", name: "Feed Upload" }, occurrence_date: "2026-10-05",
    scheduled_start: "2026-10-05T10:00:00+05:30", deadline: "2026-10-05T12:00:00+05:30", status: "PENDING",
    sla_state: "WARNING", sla_note: null, remaining_seconds: 3630, completed_at: null, completion_result: null,
    is_overdue: false, assignee: { id: 10, full_name: "Rahul Sharma" }, ...overrides,
  };
}

// Frontend types for the Phase 1, 2 and 3 APIs.

export const ROLE_NAMES = ["Employee", "Operations Manager", "HR", "Admin"] as const;
export type RoleName = (typeof ROLE_NAMES)[number];

export const PERM = {
  manageUsers: "accounts.manage_users",
  viewAuditLog: "audit.view_audit_log",
  viewAllEmployees: "org.view_all_employees",
  viewTeamEmployees: "org.view_team_employees",
  manageEmployees: "org.manage_employees",
  linkEmployeeLogin: "org.link_employee_login",
  manageDepartments: "org.manage_departments",
  createTask: "tasks.create_task",
  assignTask: "tasks.assign",
  viewAllTasks: "tasks.view_all_tasks",
  viewTeamTasks: "tasks.view_team_tasks",
  manageTeamTasks: "tasks.manage_team_tasks",
  manageAllTasks: "tasks.manage_all_tasks",
  manageSlaRules: "sla.manage_sla_rules",
  editAllTasks: "tasks.edit_all_tasks",
  deleteTask: "tasks.delete_task",
  manageTaskCategories: "tasks.manage_task_categories",
  viewAllResponsibilities: "recurring.view_all_responsibilities",
  manageTeamResponsibilities: "recurring.manage_team_responsibilities",
  manageAllResponsibilities: "recurring.manage_all_responsibilities",
  manageSchedules: "recurring.manage_schedules",
  manageCompanyCalendar: "calendars.manage_company_calendar",
  // Phase 9 overdue cases (employees see their own cases without a permission).
  viewAllOverdueCases: "overdue.view_all_cases",
  reviewTeamOverdueCases: "overdue.review_team_cases",
  reviewAllOverdueCases: "overdue.review_all_cases",
} as const;

export interface User {
  id: number;
  email: string;
  first_name: string;
  last_name: string;
  full_name: string;
  roles: RoleName[];
  is_active: boolean;
  last_login: string | null;
  date_joined: string;
}

export interface Me extends User { permissions: string[]; }
export interface Paginated<T> { count: number; next: string | null; previous: string | null; results: T[]; }
export interface Role { name: RoleName; permissions: string[]; }
export interface AuditEntry { id: number; occurred_at: string; actor_user: number | null; actor_email: string | null; action: string; entity_type: string; entity_id: string; old_value: unknown; new_value: unknown; ip: string | null; request_id: string | null; context: Record<string, unknown>; }
export interface UserCreateInput { email: string; first_name: string; last_name: string; roles: RoleName[]; password: string; }
export interface UserUpdateInput { first_name?: string; last_name?: string; roles?: RoleName[]; is_active?: boolean; }

export interface Department { id: number; code: string; name: string; is_live: boolean; created_at: string; updated_at: string; }
export interface DepartmentInput { code: string; name: string; is_live?: boolean; }
export interface EmployeeRef { id: number; full_name: string; }
export interface LoginRef { id: number; email: string; is_active: boolean; }
export interface Employee { id: number; employee_code: string | null; full_name: string; email: string; department: { id: number; code: string; name: string }; reporting_manager: EmployeeRef | null; designation: string; date_of_joining: string | null; is_active: boolean; user: LoginRef | null; version: number; created_at: string; updated_at: string; }
export interface EmployeeInput { full_name: string; email: string; department: number; employee_code?: string | null; reporting_manager?: number | null; designation?: string; date_of_joining?: string | null; }
export interface EmployeeUpdateInput extends Partial<EmployeeInput> { version: number; is_active?: boolean; }
export interface DailyLogin { work_date: string; first_login_at: string; }

// --- Phase 3: tasks (the backend decides everything; React only displays it) ---
export const TASK_STATUSES = ["PENDING", "IN_PROGRESS", "BLOCKED", "COMPLETED", "CANCELLED"] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];
export const TASK_PRIORITIES = ["LOW", "MEDIUM", "HIGH", "URGENT"] as const;
export type TaskPriority = (typeof TASK_PRIORITIES)[number];
export const TASK_TYPES = ["ADHOC", "REGULAR", "INCENTIVE"] as const; // RECURRING is scheduler-only
export type TaskType = (typeof TASK_TYPES)[number] | "RECURRING";
export const RECEIVED_SOURCES = ["EMAIL", "API", "IMPORT", "MANUAL", "SYSTEM"] as const;
export type ReceivedSource = (typeof RECEIVED_SOURCES)[number];
export type VerificationStatus = "NOT_REQUIRED" | "PENDING" | "VERIFIED" | "REJECTED";
export type TaskAction =
  | "edit" | "change_department" | "delete" | "reassign" | "cancel" | "acknowledge" | "start" | "complete"
  | "block" | "unblock" | "verify" | "reject_verification" | "comment" | "attach";
export type TaskView = "received" | "sent" | "all";

export interface UserRef { id: number; email: string; full_name: string; }
export interface DepartmentRef { id: number; code: string; name: string; }

// --- SLA (calculated by the backend; React only displays these values) ---
export type SlaState = "NOT_STARTED" | "ON_TRACK" | "WARNING" | "CRITICAL" | "OVERDUE";
export interface SlaClock {
  kind: "ACK" | "RESOLUTION"; rule_code: string; rule_name: string; rule_version: number;
  rule_type: string; clock: string; trigger: string; duration_minutes: number | null;
  start_at: string | null; due_at: string | null; state: SlaState; elapsed_pct: number | null;
  remaining_seconds: number | null; warning_at: string | null; critical_at: string | null;
  overdue_at: string | null; stopped_at: string | null; stop_reason: string | null;
  outcome: "MET" | "MISSED" | null; waiting_for: string | null;
}
export interface TaskSla { resolution: SlaClock | null; resolution_note: string | null; acknowledgment: SlaClock | null; }
export interface TemplateRef { id: number; code: string; name: string; }
export interface CategoryRef { id: number; code: string; name: string; }
export interface TaskCategory extends CategoryRef { is_active: boolean; created_at: string; updated_at: string; }
export interface TaskTemplate {
  id: number; code: string; name: string; department: DepartmentRef; trigger: string;
  fixed_time: string | null; resolution_rule_code: string; sla_rule_name: string | null;
  sla_note: string | null; acknowledgment_required: boolean; verification_required: boolean;
}
export interface AppNotification {
  id: number; kind: "SLA_WARNING" | "SLA_CRITICAL" | "SLA_OVERDUE"; title: string; body: string;
  task: number | null; task_reference: string | null; created_at: string; read_at: string | null;
  email_status: string;
}

export type TaskSource = "MANUAL" | "SCHEDULED";
export interface ResponsibilityRef { id: number; code: string; name: string; }
export interface ScheduleRef { id: number; title: string; frequency: string; }
export interface Task {
  id: number; reference: string; title: string; description: string; task_type: TaskType;
  /** Phase 5: SCHEDULED = generated from a responsibility; MANUAL = created / assigned by a user. */
  source: TaskSource; responsibility: ResponsibilityRef | null; schedule: ScheduleRef | null;
  occurrence_date: string | null; generated_at: string | null;
  template: TemplateRef | null; category: CategoryRef | null; trigger_at: string | null; sla: TaskSla;
  priority: TaskPriority; status: TaskStatus; department: DepartmentRef; created_by: UserRef;
  assigned_to: EmployeeRef; assigned_by: UserRef; assigned_at: string;
  received_at: string | null; received_at_source: ReceivedSource | null;
  acknowledgment_required: boolean; acknowledged_at: string | null; started_at: string | null;
  completed_at: string | null; completed_by: UserRef | null; completion_source: string | null;
  verification_required: boolean; verification_status: VerificationStatus; rework_count: number;
  blocked_reason: string; blocked_at: string | null; cancelled_reason: string; cancelled_at: string | null;
  version: number; created_at: string; updated_at: string; allowed_actions: TaskAction[];
}
export interface TaskAssignment { id: number; from_employee: EmployeeRef | null; to_employee: EmployeeRef; assigned_by: UserRef; assigned_at: string; note: string; }
export interface TaskVerification { cycle_no: number; submitted_at: string; decision: "VERIFIED" | "REJECTED"; rejection_reason: string; remarks: string; decided_by: UserRef; decided_at: string; rework_seconds: number | null; }
export interface TaskDetail extends Task { assignments: TaskAssignment[]; verifications: TaskVerification[]; }
export interface TaskComment { id: number; author: UserRef; body: string; created_at: string; }
export interface TaskAttachment { id: number; original_filename: string; size_bytes: number; sha256: string; uploaded_by: UserRef; created_at: string; }
export interface AssigneeOption { id: number; full_name: string; department: DepartmentRef; }
export interface TaskCreateInput {
  title: string; description?: string; department: number; category: number;
  template?: number | null; task_type?: TaskType | null;
  priority?: TaskPriority; assigned_to: number; trigger_at?: string | null;
  received_at?: string | null; received_at_source?: ReceivedSource | null;
  acknowledgment_required?: boolean | null; verification_required?: boolean | null;
}
export interface SlaPreviewInput {
  template?: number | null; assigned_to?: number | null; acknowledgment_required?: boolean | null;
  trigger_at?: string | null;
  /** Phase 5.2: a configured priority SLA rule applies to manually raised tasks. */
  priority?: TaskPriority | null;
}

// --- Phase 5: responsibilities, schedules, occurrences, Company Calendar ---
export interface ResponsibilityOwner {
  id: number; employee: EmployeeRef; effective_from: string; effective_to: string | null;
  note: string; assigned_by: UserRef; created_at: string;
  /** Phase 5.2: set when a same-day correction replaced this (mistaken) period. */
  superseded_at?: string | null; superseded_by?: UserRef | null;
}
export interface Responsibility {
  id: number; code: string; name: string; description: string; department: DepartmentRef;
  category: CategoryRef; template: TemplateRef | null; priority: TaskPriority; is_active: boolean;
  current_owner: ResponsibilityOwner | null; version: number; created_at: string; updated_at: string;
  /** Phase A (server-decided): active, not-ended schedules (0 = nothing will be generated). */
  active_schedule_count: number;
  /** Phase A (server-decided): may the signed-in user manage it (scope included). */
  can_manage: boolean;
  /** Responsibility deadline (SLA) in minutes, set by HR / Admin; null = none (existing SLA applies). */
  deadline_minutes?: number | null;
  /** Server-decided: may the signed-in user define the deadline (HR / Admin, active only). */
  can_manage_deadline?: boolean;
}
export interface ResponsibilityInput {
  code: string; name: string; description?: string; department: number; category: number;
  template?: number | null; priority?: TaskPriority;
}
export type Frequency = "DAILY" | "MONTHLY" | "WEEKLY" | "ONCE"; // Phase B: WEEKLY, ONCE
export type NonWorkingDayPolicy = "SKIP" | "NEXT_WORKING_DAY" | "PREVIOUS_WORKING_DAY";
export interface RecurringSchedule {
  id: number; responsibility: ResponsibilityRef; title: string; description: string;
  frequency: Frequency; run_time: string; day_of_month: number | null;
  non_working_day_policy: NonWorkingDayPolicy; is_active: boolean; effective_from: string;
  effective_to: string | null; version: number; created_at: string; updated_at: string;
  /** Phase A (server-decided): may the signed-in user edit it. */
  can_manage: boolean;
  /** Phase B: WEEKLY weekdays (0=Monday .. 6=Sunday) and the ONCE date (always sent by the API). */
  weekdays?: number[]; run_date?: string | null;
}
/** Phase A: schedule fields for setup and "Add schedule" (DAILY / MONTHLY only). */
export interface ScheduleInput {
  title?: string; description?: string; frequency: Frequency; run_time: string;
  day_of_month?: number | null; non_working_day_policy?: NonWorkingDayPolicy;
  /** Omitted for ONCE: the server derives that window from run_date (Phase B). */
  effective_from?: string; effective_to?: string | null;
  weekdays?: number[]; run_date?: string | null; // Phase B
}
export interface ScheduleCreateInput extends ScheduleInput { responsibility: number; title: string; }
/** Phase A: POST /responsibilities/setup/ — responsibility + optional owner + first schedule. */
export interface ResponsibilitySetupInput extends ResponsibilityInput {
  owner?: { employee: number; effective_from: string; note?: string } | null;
  schedule: ScheduleInput;
  /** Optional responsibility deadline (SLA) in minutes; HR / Admin only. */
  deadline_minutes?: number | null;
}
export type OccurrenceStatus = "GENERATED" | "SKIPPED" | "MISSED" | "FAILED";
export interface ScheduleOccurrence {
  id: number; schedule: number; schedule_title: string; responsibility: ResponsibilityRef;
  occurrence_date: string; status: OccurrenceStatus;
  task: { id: number; reference: string; status: string } | null; assignee: EmployeeRef | null;
  generated_at: string | null; detail: string; created_at: string;
}
export type CalendarDayKind = "HOLIDAY" | "SPECIAL_WORKING_DAY";
export interface CalendarDay { id: number; date: string; kind: CalendarDayKind; name: string; created_at: string; }
export interface CompanyCalendar {
  code: string; name: string; weekly_off_weekdays: number[]; saturday_working_occurrences: number[];
  days: CalendarDay[];
}

// --- Phase 5.1: daily activities and Admin (Boss) monitoring ---
export type CompletionResult = "ON_TIME" | "LATE" | "NO_DEADLINE";
export interface DailyActivity {
  task_id: number; reference: string; title: string; responsibility: ResponsibilityRef | null;
  occurrence_date: string | null; scheduled_start: string | null; deadline: string | null;
  status: TaskStatus; sla_state: SlaState | null; sla_note: string | null;
  /** Seconds to the deadline when the server answered (negative = overdue). */
  remaining_seconds: number | null; completed_at: string | null;
  completion_result: CompletionResult | null; is_overdue: boolean; assignee: EmployeeRef;
}
export interface DailyActivityList { date: string; server_time: string; activities: DailyActivity[]; }
export interface WorkCounts { total: number; completed: number; pending: number; overdue: number; completed_late: number; }
export interface MonitoredEmployee extends EmployeeRef { department: DepartmentRef; }
export interface EmployeeWorkSummary { employee: MonitoredEmployee; daily_activity: WorkCounts; assigned_tasks: WorkCounts; }
export interface OperationsSummary { date: string; server_time: string; employees: EmployeeWorkSummary[]; }
/** Operations Manager team view: the same summary for their own department (forced by the server). */
export interface TeamOperationsSummary extends OperationsSummary { department: DepartmentRef; }
export interface AssignedTaskRow {
  task_id: number; reference: string; title: string; priority: TaskPriority;
  raised_by: UserRef; department: DepartmentRef; category: CategoryRef | null; assigned_at: string;
  assigned_by: UserRef; deadline: string | null; status: TaskStatus; sla_state: SlaState | null;
  completed_at: string | null; completion_result: CompletionResult | null; completed_on_day: boolean; is_overdue: boolean;
}
export interface EmployeeDailyActivities { date: string; server_time: string; employee: MonitoredEmployee; counts: WorkCounts; activities: DailyActivity[]; }
export interface EmployeeAssignedTasks { date: string; server_time: string; employee: MonitoredEmployee; counts: WorkCounts; tasks: AssignedTaskRow[]; }

// --- Phase 6A: Admin (Boss) Command Center (read-only) ---
/** Never assumed healthy: HEALTHY, FAILED, NEVER_RUN or UNKNOWN. */
export type HealthStatus = "HEALTHY" | "FAILED" | "NEVER_RUN" | "UNKNOWN";
export interface SchedulerJob {
  job: string; name: string; status: HealthStatus; detail: string; last_started_at: string | null;
  last_finished_at: string | null; last_success_at: string | null; last_summary: Record<string, number>;
}
export interface SlaCounts {
  not_started: number; on_track: number; warning: number; critical: number; overdue: number;
  completed_on_time: number; completed_late: number;
}
export interface RecentEvent { id: number; action: string; entity_type: string; entity_id: string; actor: UserRef | null; occurred_at: string; }
export interface CommandCenterSummary {
  date: string; server_time: string; scheduler: SchedulerJob[];
  todays_occurrences: { generated: number; skipped: number; missed: number; failed: number };
  operations: { daily_activity: WorkCounts; assigned_tasks: WorkCounts };
  sla: { daily_activity: SlaCounts; assigned_tasks: SlaCounts };
  overview: CommandCenterOverview;
  employees: CommandCenterEmployee[]; recent_events: RecentEvent[];
}
export interface HealthCheck { name: string; status: HealthStatus; detail: string; }
export interface CommandCenterHealth { checked_at: string; overall: HealthStatus; checks: HealthCheck[]; }

// --- Phase 6B: read-only operational monitoring ---
/** Factual states of open work (not a score): OVERDUE, CRITICAL, WARNING, BLOCKED, ON_TRACK, NO_ACTIVE_WORK. */
export type AttentionState = "OVERDUE" | "CRITICAL" | "WARNING" | "BLOCKED" | "ON_TRACK" | "NO_ACTIVE_WORK";
export type WorkSource = "SCHEDULED" | "MANUAL";
export interface CommandCenterEmployee extends EmployeeWorkSummary { attention: AttentionState[]; }
export interface CommandCenterOverview {
  employees: { total_active: number; with_work: number };
  daily_activities: { scheduled: number; completed: number; pending: number; in_progress: number; blocked: number; overdue: number };
  assigned_tasks: { total: number; active: number; completed: number; pending: number; in_progress: number; blocked: number; overdue: number };
  sla: { on_track: number; warning: number; critical: number; overdue: number };
}
export interface CommandCenterFilters {
  date?: string; department?: string; employee?: string; source?: WorkSource | ""; status?: TaskStatus | ""; sla_state?: SlaState | "";
}
export interface SlaAttentionItem {
  employee: MonitoredEmployee; task_id: number; reference: string; title: string; source: WorkSource;
  status: TaskStatus; deadline: string | null; sla_state: SlaState; remaining_seconds: number | null;
}
export interface SlaAttention {
  date: string; server_time: string; critical: SlaAttentionItem[]; warning: SlaAttentionItem[]; overdue: SlaAttentionItem[];
  on_track: SlaAttentionItem[]; not_started: SlaAttentionItem[];
}
export interface CommandCenterEmployeeDetail {
  date: string; server_time: string; employee: MonitoredEmployee; attention: AttentionState[];
  daily_activities: (DailyActivity & { source: WorkSource })[];
  assigned_tasks: (AssignedTaskRow & { source: WorkSource; remaining_seconds: number | null })[];
}

// --- Phase 9: overdue cases (exact backend contract: apps/overdue/api/serializers.py) ---
/** One controlled vocabulary for the employee's reason and the reviewer's authoritative cause. */
export const OVERDUE_CAUSES = ["DEPENDENCY", "EMPLOYEE", "SYSTEM", "CLIENT", "OTHER"] as const;
export type OverdueCause = (typeof OVERDUE_CAUSES)[number];
export type OverdueStatus = "OPEN" | "REASON_SUBMITTED" | "REVIEWED";
export type OverdueOpenedVia = "TICK" | "COMPLETION" | "REPAIR";
/** The overdue API's user reference has no full_name (unlike UserRef). */
export interface OverdueUserRef { id: number; email: string; }
export interface OverdueEmployeeRef extends EmployeeRef { employee_code: string | null; }
export interface OverdueCase {
  id: number; status: OverdueStatus; opened_at: string; opened_via: OverdueOpenedVia;
  // Facts copied when the case opened (read-only). The employee is the assignee at breach time.
  task_id: number; task_reference: string; task_title: string; employee: OverdueEmployeeRef;
  department: DepartmentRef; task_creator: OverdueUserRef; task_assigned_at: string;
  priority: TaskPriority; category_name: string; sla_start_at: string; sla_due_at: string;
  sla_rule_code: string; sla_rule_name: string; overdue_at: string; completed_at: string | null;
  /** Server-computed: frozen at completion, otherwise counted to now. */
  overdue_minutes: number;
  // The employee's reason (never the final cause). "" until submitted.
  reason_category: OverdueCause | ""; explanation: string; submitted_at: string | null; submitted_by: OverdueUserRef | null;
  // The reviewer's authoritative cause. "" until reviewed.
  cause: OverdueCause | ""; review_remark: string; reviewed_at: string | null; reviewed_by: OverdueUserRef | null;
  version: number;
  /** Server-decided for the signed-in user (scope, ownership, no self-review). */
  can_submit: boolean; can_review: boolean;
}
/** Backend list filters (apps/overdue/filters.py) plus StandardPagination's page / page_size. */
export interface OverdueCaseFilters {
  status?: OverdueStatus; employee?: number; department?: number; date_from?: string; date_to?: string;
  cause?: OverdueCause; reason_category?: OverdueCause; task?: number; priority?: TaskPriority;
  page?: number; page_size?: number;
}
export interface OverdueReasonInput { version: number; reason_category: OverdueCause; explanation: string; }
export interface OverdueReviewInput { version: number; cause: OverdueCause; remark: string; }

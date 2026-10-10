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
  // Phase 7.4: KRA months are read by HR (manage / finalize / reopen) and Admin (reopen).
  managePerformance: "performance.manage_performance",
  finalizePerformance: "performance.finalize_performance",
  reopenPerformance: "performance.reopen_performance",
  // Phase 7.5A: KRA configuration. HR prepares drafts; Admin activates and retires.
  configureKpis: "performance.configure_kpis",
  approveKpiConfig: "performance.approve_kpi_config",
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
export interface TaskDetail extends Task {
  assignments: TaskAssignment[]; verifications: TaskVerification[];
  /** Generated tasks: when the occurrence was scheduled; null for manual tasks. Optional for
   * responses from servers older than the scheduling fix. */
  scheduled_at?: string | null;
  /** The resolution SLA was already overdue when the task was generated (system-caused).
   * null = not a generated task, or not recorded (generated before this was tracked). */
  arrived_overdue?: boolean | null;
  /** The acknowledgement SLA was already overdue when the task was generated. */
  ack_arrived_overdue?: boolean | null;
}
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
  occurrence_date: string | null;
  /** Kept for older clients: the SLA start, or the schedule time while not started. Prefer
   * scheduled_at (the occurrence time) and sla_start_at (the effective SLA start). */
  scheduled_start: string | null; deadline: string | null;
  /** When the occurrence was scheduled (recorded at generation). */
  scheduled_at?: string | null;
  /** When the resolution SLA runs from (a hold moves it); null while not started. */
  sla_start_at?: string | null;
  /** Resolution SLA already overdue when generated (system-caused); null = not recorded. */
  arrived_overdue?: boolean | null;
  /** Acknowledgement SLA already overdue when generated; null = not recorded. */
  ack_arrived_overdue?: boolean | null;
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

// --- Phase 7.4: KRA performance (exact backend contract: apps/performance/api/kra_report_serializers.py) ---
/** What an employee sees of one of their KRA months (no raw status is ever sent). */
export type KraEmployeeState = "PROVISIONAL" | "PENDING_REVIEW" | "FINALIZED";
/** Decimals travel as strings with 6 places; the UI shows 2 (section 13). */
export interface KraMyMonthRow {
  id: number; year: number; month: number; state: KraEmployeeState;
  /** Only for PROVISIONAL and FINALIZED months; null otherwise. */
  final_total: string | null; max_points_applicable: string | null; band: string | null;
}
export interface KraMyHistory {
  employee: { id: number; full_name: string; date_of_joining: string | null };
  current: { year: number; month: number };
  months: KraMyMonthRow[];
}
export interface KraMyComponent {
  label: string; applicable: boolean; na_label: string | null; achievement_pct: string | null;
  on_time_count: number; late_count: number; overdue_count: number;
}
export interface KraMyKpi {
  name: string; weight: string; not_applicable: boolean; na_label: string | null;
  achievement_pct: string | null; auto_points: string | null; final_points: string | null;
  components: KraMyComponent[];
}
export interface KraMyDeduction { rule: string; kpi: string; component: string; points: string; }
/** PENDING_REVIEW months carry only id / year / month / state. */
export interface KraMyMonth {
  id: number; year: number; month: number; state: KraEmployeeState;
  auto_total?: string; final_total?: string; max_points_applicable?: string; band?: string;
  kpis?: KraMyKpi[]; deductions?: KraMyDeduction[];
}
export type KraAnnualExclusion = "NO_RECORD" | "NOT_FINALIZED" | "LEGACY_SCALE" | "NOTHING_APPLICABLE";
export interface KraMyAnnual {
  year: number; applicable_months: number; annual_total: string | null; annual_average: string | null;
  maximum_total: string;
  months: { id: number; month: number; final_total: string; max_points_applicable: string; band: string }[];
  excluded: { month: number; reason: KraAnnualExclusion }[];
}
/** The existing legacy (0-100) report row (apps/performance/api/serializers.py), fields used here. */
export interface LegacyPerformanceRow {
  id: number; employee: { id: number; employee_id: string; full_name: string };
  year: number; month: number; status: string; overall_score: string | null; performance_band: string;
}
export interface KraListRow {
  id: number;
  employee: { id: number; employee_code: string | null; full_name: string };
  /** The department recorded when the month was calculated. */
  department: { id: number; code: string; name: string } | null;
  year: number; month: number; status: string; provisional: boolean; reopen_count: number;
  max_points_applicable: string | null; auto_total: string | null; adjustment_total: string | null;
  deduction_total: string | null; final_total: string | null; band: string; band_ceiling: string;
  finalized_at: string | null;
}
export interface KraListFilters {
  year?: number; month?: number; employee?: number; department?: number; status?: string;
  band?: string; provisional?: "true" | "false"; page?: number; page_size?: number;
}
/** The HR / Admin month detail (apps/performance/api/review_serializers.py KraRevMonthSerializer). */
export interface KraReviewMonth {
  id: number; employee: number; year: number; month: number; status: string; version: number;
  plan_version: number | null; cutoff_at: string | null; provisional: boolean;
  max_points_applicable: string | null; auto_total: string | null; adjustment_total: string | null;
  deduction_total: string | null; final_total: string | null; band: string; band_ceiling: string;
  reopen_count: number; reviewed_at: string | null; finalized_at: string | null;
  kpis: {
    kpi_id: number; code: string; name: string; weight: string; not_applicable: boolean; na_reason: string;
    auto_points: string | null; adjustment_points: string; deduction_points: string; final_points: string | null;
  }[];
  deduction_applications: {
    id: number; rule_code: string; rule_name: string; kind: string; scope: string; kpi_id: number | null;
    component_id: number | null; percent: string | null; ceiling_band: string; evidence: string; reason: string;
    applied_at: string; reverses: number | null; reversed_by: number | null; active: boolean;
  }[];
  deduction_lines: {
    scope: string; rule_code: string; rule_name: string; kpi: string; component: string;
    rate_pct: string | null; effective: boolean; points: string; ceiling_band?: string;
  }[];
  blockers: { kind: string; component: string; message: string }[];
}

/** Phase 7.5A KRA configuration (apps/performance/api/config_serializers.py). Choice values are
 * plain strings validated by the backend; decimals travel as strings and are never computed on. */
export type KpiCfgStatus = "DRAFT" | "ACTIVE" | "RETIRED";
export type KpiCfgModel = "LEGACY_WEIGHTED" | "KRA_POINTS";
export interface KpiCfgKpi { id: number; code: string; name: string; description: string; is_active: boolean; }
export interface KpiCfgVersionedRef { id: number; code: string; version: number; name: string; status: KpiCfgStatus; }
export interface KpiCfgPlanRef {
  id: number; configuration: string; version: number; name: string; status: KpiCfgStatus; calculation_model: KpiCfgModel;
}
export interface KpiCfgComponent {
  id: number; plan_line: number; position: number; source_type: string;
  responsibility: { id: number; code: string; name: string } | null; label: string;
  contribution_share: string; task_scope: string; manual_match: string; verification_policy: string;
}
export interface KpiCfgLine {
  id: number; plan_version: number; kpi: { id: number; code: string; name: string }; name: string;
  display_name: string; weight: string; scoring_rule: KpiCfgVersionedRef | null; position: number;
  components: KpiCfgComponent[];
}
export interface KpiCfgDeductionRule {
  id: number; plan_version: number; code: string; name: string; kind: string; scope: string;
  min_pct: string | null; max_pct: string | null;
  ceiling_band: { id: number; name: string; min_points: string } | null;
  stacking: string; cap_pct: string | null; uncapped: boolean; priority: number | null; description: string;
}
export interface KpiCfgPlan {
  id: number; configuration: string; version: number; name: string; calculation_model: KpiCfgModel;
  status: KpiCfgStatus; effective_from: string; effective_to: string | null; band_scheme: KpiCfgVersionedRef | null;
  credit_on_time: string | null; credit_late: string | null; credit_overdue: string | null;
  deduction_stacking_method: string; created_at: string; activated_at: string | null;
  retired_at: string | null; retire_reason: string;
}
export interface KpiCfgPlanDetail extends KpiCfgPlan { lines: KpiCfgLine[]; deduction_rules: KpiCfgDeductionRule[]; }
export interface KpiCfgReadiness {
  plan_id: number; status: KpiCfgStatus; calculation_model: KpiCfgModel; ready: boolean;
  problems: string[]; checked_on: string;
}
export interface KpiCfgStep { min_achievement_pct: string; score_pct: string; }
export interface KpiCfgScoringRule {
  id: number; code: string; version: number; name: string; status: KpiCfgStatus; effective_from: string;
  effective_to: string | null; below_min_score_pct: string | null; steps: KpiCfgStep[];
  activated_at: string | null; retired_at: string | null; retire_reason: string;
}
export interface KpiCfgBand { id: number; name: string; min_points: string; position: number; }
export interface KpiCfgBandScheme {
  id: number; code: string; version: number; name: string; status: KpiCfgStatus; effective_from: string;
  effective_to: string | null; bands: KpiCfgBand[]; activated_at: string | null; retired_at: string | null;
  retire_reason: string;
}
export interface KpiCfgPlanDefault {
  id: number; department: { id: number; code: string; name: string }; role: string; configuration: string;
  effective_from: string; effective_to: string | null; created_at: string; ended_at: string | null;
}
export interface KpiCfgOverride {
  id: number; employee: { id: number; full_name: string }; plan_version: KpiCfgPlanRef;
  effective_from: string; effective_to: string | null; reason: string; created_at: string;
}
export interface KpiCfgResolution {
  employee: { id: number; full_name: string }; date: string; state: "RESOLVED" | "NO_PLAN" | "AMBIGUOUS_ROLE";
  source: "OVERRIDE" | "DEFAULT" | null; reason: string; roles: string[]; plan_version: KpiCfgPlanRef | null;
  override_id: number | null; default_id: number | null; matching_default_ids: number[];
}

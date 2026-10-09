import { api, apiUpload } from "./apiClient";
import type { AppNotification, AssigneeOption, AuditEntry, CalendarDay, CommandCenterEmployeeDetail, CommandCenterFilters, CommandCenterHealth, CommandCenterSummary, SlaAttention, DailyActivityList, EmployeeAssignedTasks, EmployeeDailyActivities, OperationsSummary, TeamOperationsSummary, CalendarDayKind, CompanyCalendar, DailyLogin, Department, DepartmentInput, Employee, EmployeeInput, EmployeeUpdateInput, Me, OverdueCase, OverdueCaseFilters, OverdueReasonInput, OverdueReviewInput, Paginated, RecurringSchedule, Responsibility, ResponsibilityInput, ResponsibilityOwner, ResponsibilitySetupInput, Role, ScheduleCreateInput, ScheduleOccurrence, Task, TaskAttachment, TaskComment, TaskCategory, TaskCreateInput, TaskDetail, TaskSla, TaskTemplate, TaskView, SlaPreviewInput, User, UserCreateInput, UserUpdateInput } from "./types";

export const authApi = {
  me: () => api<Me>("/auth/me/"),
  login: (email: string, password: string) => api<Me>("/auth/login/", { method: "POST", body: { email, password } }),
  logout: () => api<void>("/auth/logout/", { method: "POST" }),
  changePassword: (current_password: string, new_password: string) => api<void>("/auth/password-change/", { method: "POST", body: { current_password, new_password } }),
};

export interface UserListParams { page?: number; search?: string; role?: string; is_active?: string; }
export const usersApi = {
  list: (params: UserListParams) => api<Paginated<User>>("/users/", { query: { ...params } }),
  create: (input: UserCreateInput) => api<User>("/users/", { method: "POST", body: input }),
  update: (id: number, input: UserUpdateInput) => api<User>(`/users/${id}/`, { method: "PATCH", body: input }),
  setPassword: (id: number, password: string) => api<void>(`/users/${id}/set-password/`, { method: "POST", body: { password } }),
};
export const rolesApi = { list: () => api<Role[]>("/roles/") };

export interface AuditParams { page?: number; action?: string; entity_type?: string; entity_id?: string; from?: string; to?: string; }
export const auditApi = { list: (params: AuditParams) => api<Paginated<AuditEntry>>("/audit-log/", { query: { ...params } }) };

export interface EmployeeListParams { page?: number; search?: string; department?: number | string; is_active?: boolean | string; has_login?: boolean | string; }
export interface LoginListParams { page?: number; from?: string; to?: string; }

export const departmentsApi = {
  list: () => api<Department[]>("/departments/"),
  create: (input: DepartmentInput) => api<Department>("/departments/", { method: "POST", body: input }),
  update: (id: number, input: Partial<DepartmentInput>) => api<Department>(`/departments/${id}/`, { method: "PATCH", body: input }),
};

export const employeesApi = {
  list: (params: EmployeeListParams) => api<Paginated<Employee>>("/employees/", { query: { ...params } }),
  /** The signed-in user's own employee record (404 "no_employee_record" when there is none). */
  me: () => api<Employee>("/employees/me/"),
  create: (input: EmployeeInput) => api<Employee>("/employees/", { method: "POST", body: input }),
  update: (id: number, input: EmployeeUpdateInput) => api<Employee>(`/employees/${id}/`, { method: "PATCH", body: input }),
  linkLogin: (id: number, version: number, user: number) => api<Employee>(`/employees/${id}/link-login/`, { method: "POST", body: { version, user } }),
  unlinkLogin: (id: number, version: number) => api<Employee>(`/employees/${id}/unlink-login/`, { method: "POST", body: { version } }),
  logins: (id: number, params: LoginListParams = {}) => api<Paginated<DailyLogin>>(`/employees/${id}/logins/`, { query: { ...params } }),
};

export interface TaskListParams {
  page?: number; view?: TaskView; status?: string; priority?: string; assignee?: number | string;
  department?: number | string; search?: string; from?: string; to?: string;
  /** Phase 5: "scheduled" (generated from a responsibility) or "manual". */
  source?: "scheduled" | "manual" | "";
}
type ActionBody = { version: number } & Record<string, unknown>;

export const tasksApi = {
  list: (params: TaskListParams) => api<Paginated<Task>>("/tasks/", { query: { ...params } }),
  get: (id: number) => api<TaskDetail>(`/tasks/${id}/`),
  create: (input: TaskCreateInput) => api<TaskDetail>("/tasks/", { method: "POST", body: input }),
  update: (id: number, body: ActionBody) => api<TaskDetail>(`/tasks/${id}/`, { method: "PATCH", body }),
  action: (id: number, name: string, body: ActionBody) =>
    api<TaskDetail>(`/tasks/${id}/${name}/`, { method: "POST", body }),
  assignees: () => api<AssigneeOption[]>("/tasks/assignees/"),
  templates: () => api<TaskTemplate[]>("/task-templates/"),
  /** Phase 5.1: my generated daily activities (reading never creates any). */
  dailyActivities: (date?: string) => api<DailyActivityList>("/tasks/daily-activities/", { query: { date } }),
  categories: () => api<TaskCategory[]>("/task-categories/"),
  /** Physical delete (HR / Admin). The current version guards against deleting a newer task. */
  remove: (id: number, version: number) =>
    api<void>(`/tasks/${id}/`, { method: "DELETE", query: { version } }),
  slaPreview: (input: SlaPreviewInput) =>
    api<TaskSla>("/tasks/sla-preview/", { method: "POST", body: input }),
  comments: (id: number) => api<TaskComment[]>(`/tasks/${id}/comments/`),
  addComment: (id: number, body: string) =>
    api<TaskComment>(`/tasks/${id}/comments/`, { method: "POST", body: { body } }),
  attachments: (id: number) => api<TaskAttachment[]>(`/tasks/${id}/attachments/`),
  upload: (id: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiUpload<TaskAttachment>(`/tasks/${id}/attachments/`, form);
  },
  downloadUrl: (id: number, attachmentId: number) =>
    `/api/v1/tasks/${id}/attachments/${attachmentId}/download/`,
};

export const notificationsApi = {
  list: (params: { page?: number; unread?: boolean } = {}) =>
    api<Paginated<AppNotification>>("/notifications/", { query: { ...params } }),
  unreadCount: () => api<{ unread: number }>("/notifications/unread-count/"),
  markRead: (id: number) => api<AppNotification>(`/notifications/${id}/read/`, { method: "POST" }),
  markAllRead: () => api<{ marked: number }>("/notifications/read-all/", { method: "POST" }),
};

// --- Phase 5 ---
export const responsibilitiesApi = {
  list: () => api<Responsibility[]>("/responsibilities/"),
  create: (input: ResponsibilityInput) => api<Responsibility>("/responsibilities/", { method: "POST", body: input }),
  update: (id: number, input: { version: number } & Record<string, unknown>) =>
    api<Responsibility>(`/responsibilities/${id}/`, { method: "PATCH", body: input }),
  owners: (id: number) => api<ResponsibilityOwner[]>(`/responsibilities/${id}/owners/`),
  assignOwner: (id: number, input: { employee: number; effective_from: string; note?: string }) =>
    api<ResponsibilityOwner>(`/responsibilities/${id}/owners/`, { method: "POST", body: input }),
  endOwnership: (id: number, input: { last_day: string; note?: string }) =>
    api<ResponsibilityOwner>(`/responsibilities/${id}/end-ownership/`, { method: "POST", body: input }),
  /** Phase A: responsibility + optional owner + first schedule in one transaction. */
  setup: (input: ResponsibilitySetupInput) =>
    api<Responsibility>("/responsibilities/setup/", { method: "POST", body: input }),
};
export const schedulesApi = {
  list: () => api<RecurringSchedule[]>("/recurring-schedules/"),
  /** Phase A: add a schedule to an existing responsibility. */
  create: (input: ScheduleCreateInput) => api<RecurringSchedule>("/recurring-schedules/", { method: "POST", body: input }),
  update: (id: number, input: { version: number } & Record<string, unknown>) =>
    api<RecurringSchedule>(`/recurring-schedules/${id}/`, { method: "PATCH", body: input }),
  occurrences: (params: { page?: number; status?: string }) =>
    api<Paginated<ScheduleOccurrence>>("/schedule-occurrences/", { query: { ...params } }),
};
export const calendarApi = {
  get: () => api<CompanyCalendar>("/calendars/company/"),
  addDay: (input: { date: string; kind: CalendarDayKind; name: string }) =>
    api<CalendarDay>("/calendars/company/days/", { method: "POST", body: input }),
  removeDay: (id: number) => api<void>(`/calendars/company/days/${id}/`, { method: "DELETE" }),
};
export interface OperationsParams { date?: string; department?: number | string; employee?: number | string; status?: string; sla_state?: string; }
export const operationsApi = {
  summary: (params: OperationsParams) => api<OperationsSummary>("/operations/daily-summary/", { query: { ...params } }),
  dailyActivities: (id: number, params: OperationsParams) =>
    api<EmployeeDailyActivities>(`/operations/employees/${id}/daily-activities/`, { query: { ...params } }),
  assignedTasks: (id: number, params: OperationsParams) =>
    api<EmployeeAssignedTasks>(`/operations/employees/${id}/assigned-tasks/`, { query: { ...params } }),
  // Operations Manager: own department only; the server forces the department.
  teamSummary: (params: OperationsParams) => api<TeamOperationsSummary>("/operations/team/daily-summary/", { query: { ...params } }),
  teamDailyActivities: (id: number, params: OperationsParams) =>
    api<EmployeeDailyActivities>(`/operations/team/employees/${id}/daily-activities/`, { query: { ...params } }),
  teamAssignedTasks: (id: number, params: OperationsParams) =>
    api<EmployeeAssignedTasks>(`/operations/team/employees/${id}/assigned-tasks/`, { query: { ...params } }),
};
export const commandCenterApi = {
  summary: (filters: CommandCenterFilters = {}) => api<CommandCenterSummary>("/command-center/summary/", { query: { ...filters } }),
  health: () => api<CommandCenterHealth>("/command-center/health/"),
  slaAttention: (filters: CommandCenterFilters = {}) => api<SlaAttention>("/command-center/sla-attention/", { query: { ...filters } }),
  employee: (id: number, filters: CommandCenterFilters = {}) =>
    api<CommandCenterEmployeeDetail>(`/command-center/employees/${id}/`, { query: { ...filters, department: undefined, employee: undefined } }),
};

/** Phase 9 overdue cases. The backend decides who sees what; a case outside scope is a 404. */
export const overdueCasesApi = {
  list: (filters: OverdueCaseFilters = {}) => api<Paginated<OverdueCase>>("/overdue-cases/", { query: { ...filters } }),
  /** Cases waiting for MY review (reason submitted, in my scope, never my own). */
  reviewQueue: (filters: OverdueCaseFilters = {}) =>
    api<Paginated<OverdueCase>>("/overdue-cases/", { query: { ...filters, reviewable: 1 } }),
  get: (id: number) => api<OverdueCase>(`/overdue-cases/${id}/`),
  submit: (id: number, input: OverdueReasonInput) =>
    api<OverdueCase>(`/overdue-cases/${id}/submit/`, { method: "POST", body: input }),
  review: (id: number, input: OverdueReviewInput) =>
    api<OverdueCase>(`/overdue-cases/${id}/review/`, { method: "POST", body: input }),
};

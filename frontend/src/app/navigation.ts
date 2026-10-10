import { PERM } from "../api/types";

export interface NavItem { label: string; to: string; perm?: string;
  anyPerms?: string[]; }
export const NAV_ITEMS: NavItem[] = [
  { label: "Home", to: "/" },
  { label: "My profile", to: "/profile" },
  { label: "Tasks", to: "/tasks", anyPerms: [PERM.createTask, PERM.viewAllTasks, PERM.viewTeamTasks] },
  { label: "My overdue cases", to: "/overdue-cases" },
  { label: "Overdue review queue", to: "/overdue-cases/review", anyPerms: [PERM.reviewTeamOverdueCases, PERM.reviewAllOverdueCases] },
  { label: "Responsibilities", to: "/responsibilities", anyPerms: [PERM.viewAllResponsibilities, PERM.manageTeamResponsibilities, PERM.manageAllResponsibilities] },
  { label: "Schedules", to: "/schedules", anyPerms: [PERM.viewAllResponsibilities, PERM.manageTeamResponsibilities, PERM.manageAllResponsibilities] },
  { label: "Employees", to: "/employees", anyPerms: [PERM.viewAllEmployees, PERM.viewTeamEmployees] },
  { label: "KRA performance", to: "/performance/months", anyPerms: [PERM.managePerformance, PERM.finalizePerformance, PERM.reopenPerformance] },
  { label: "KRA configuration", to: "/performance/config", anyPerms: [PERM.configureKpis, PERM.approveKpiConfig] },
  { label: "Departments", to: "/departments", perm: PERM.manageDepartments },
  { label: "Users", to: "/admin/users", perm: PERM.manageUsers },
  { label: "Audit log", to: "/admin/audit", perm: PERM.viewAuditLog },
  { label: "Company calendar", to: "/admin/calendar", perm: PERM.manageCompanyCalendar },
  { label: "Operations monitor", to: "/admin/operations", anyPerms: [PERM.manageAllTasks, PERM.manageTeamTasks] },
  { label: "Command center", to: "/admin/command-center", perm: PERM.manageAllTasks },
];
export function visibleNavItems(hasPerm: (perm: string) => boolean): NavItem[] {
  return NAV_ITEMS.filter((item) => (!item.perm || hasPerm(item.perm)) && (!item.anyPerms || item.anyPerms.some(hasPerm)));
}

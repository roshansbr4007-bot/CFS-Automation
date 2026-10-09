import { Route, Routes } from "react-router-dom";
import { PERM } from "../api/types";
import { AuditLogPage } from "../features/audit/AuditLogPage";
import { CalendarPage } from "../features/calendar/CalendarPage";
import { CommandCenterPage } from "../features/commandcenter/CommandCenterPage";
import { OperationsMonitorPage } from "../features/operations/OperationsMonitorPage";
import { MyOverdueCasesPage } from "../features/overdue/MyOverdueCasesPage";
import { OverdueCaseDetailPage } from "../features/overdue/OverdueCaseDetailPage";
import { OverdueReviewQueuePage } from "../features/overdue/OverdueReviewQueuePage";
import { ResponsibilitiesPage } from "../features/recurring/ResponsibilitiesPage";
import { SchedulesPage } from "../features/recurring/SchedulesPage";
import { DepartmentsPage } from "../features/org/DepartmentsPage";
import { TaskDetailPage } from "../features/tasks/TaskDetailPage";
import { TasksPage } from "../features/tasks/TasksPage";
import { EmployeesPage } from "../features/org/EmployeesPage";
import { LoginPage } from "../features/auth/LoginPage";
import { ProfilePage } from "../features/auth/ProfilePage";
import { UsersPage } from "../features/users/UsersPage";
import { ForbiddenPage } from "../pages/ForbiddenPage";
import { HomePage } from "../pages/HomePage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { RealtimeProvider } from "../realtime/RealtimeProvider";
import { AppLayout } from "./AppLayout";
import { ProtectedRoute } from "./ProtectedRoute";

const RESPONSIBILITY_PERMS = [PERM.viewAllResponsibilities, PERM.manageTeamResponsibilities, PERM.manageAllResponsibilities];

export function AppRoutes() {
  return <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route element={<ProtectedRoute />}>
      <Route element={<RealtimeProvider><AppLayout /></RealtimeProvider>}>
        <Route index element={<HomePage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="tasks" element={<ProtectedRoute anyPerms={[PERM.createTask, PERM.viewAllTasks, PERM.viewTeamTasks]}><TasksPage /></ProtectedRoute>} />
        <Route path="tasks/:id" element={<ProtectedRoute anyPerms={[PERM.createTask, PERM.viewAllTasks, PERM.viewTeamTasks]}><TaskDetailPage /></ProtectedRoute>} />
        <Route path="overdue-cases" element={<MyOverdueCasesPage />} />
        <Route path="overdue-cases/review" element={<ProtectedRoute anyPerms={[PERM.reviewTeamOverdueCases, PERM.reviewAllOverdueCases]}><OverdueReviewQueuePage /></ProtectedRoute>} />
        <Route path="overdue-cases/:id" element={<OverdueCaseDetailPage />} />
        <Route path="responsibilities" element={<ProtectedRoute anyPerms={RESPONSIBILITY_PERMS}><ResponsibilitiesPage /></ProtectedRoute>} />
        <Route path="schedules" element={<ProtectedRoute anyPerms={RESPONSIBILITY_PERMS}><SchedulesPage /></ProtectedRoute>} />
        <Route path="admin/command-center" element={<ProtectedRoute perm={PERM.manageAllTasks}><CommandCenterPage /></ProtectedRoute>} />
        <Route path="admin/operations" element={<ProtectedRoute anyPerms={[PERM.manageAllTasks, PERM.manageTeamTasks]}><OperationsMonitorPage /></ProtectedRoute>} />
        <Route path="admin/calendar" element={<ProtectedRoute perm={PERM.manageCompanyCalendar}><CalendarPage /></ProtectedRoute>} />
        <Route path="employees" element={<ProtectedRoute anyPerms={[PERM.viewAllEmployees, PERM.viewTeamEmployees]}><EmployeesPage /></ProtectedRoute>} />
        <Route path="departments" element={<ProtectedRoute perm={PERM.manageDepartments}><DepartmentsPage /></ProtectedRoute>} />
        <Route path="admin/users" element={<ProtectedRoute perm={PERM.manageUsers}><UsersPage /></ProtectedRoute>} />
        <Route path="admin/audit" element={<ProtectedRoute perm={PERM.viewAuditLog}><AuditLogPage /></ProtectedRoute>} />
        <Route path="403" element={<ForbiddenPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Route>
  </Routes>;
}

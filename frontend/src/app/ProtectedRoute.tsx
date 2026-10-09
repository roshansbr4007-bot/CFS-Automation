import { Box, CircularProgress } from "@mui/material";
import type { ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";

interface Props { perm?: string; anyPerms?: string[]; children?: ReactNode; }
export function ProtectedRoute({ perm, anyPerms, children }: Props) {
  const { user, isLoading, hasPerm } = useAuth();
  const location = useLocation();
  if (isLoading) return <Box sx={{ display: "grid", placeItems: "center", minHeight: "40vh" }}><CircularProgress aria-label="Loading" /></Box>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  const allowed = (!perm || hasPerm(perm)) && (!anyPerms || anyPerms.some(hasPerm));
  if (!allowed) return <Navigate to="/403" replace />;
  return children ? <>{children}</> : <Outlet />;
}

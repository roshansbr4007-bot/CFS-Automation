import MenuIcon from "@mui/icons-material/Menu";
import {
  AppBar,
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemText,
  Toolbar,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { NotificationBell } from "../features/notifications/NotificationBell";
import { OverdueRealtimeRefresh } from "../features/overdue/realtimeRefresh";
import { useAuth } from "./AuthProvider";
import { visibleNavItems } from "./navigation";
import { tokens } from "./theme";

const DRAWER_WIDTH = 232;

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const { hasPerm } = useAuth();
  return (
    <List component="nav" aria-label="Main" sx={{ px: 1 }}>
      {visibleNavItems(hasPerm).map((item) => (
        <ListItemButton
          key={item.to}
          component={NavLink}
          to={item.to}
          end={item.to === "/"}
          onClick={onNavigate}
          sx={{
            borderRadius: 1,
            mb: 0.5,
            color: "rgba(255,255,255,0.78)",
            borderLeft: "3px solid transparent",
            "&.active": {
              color: "#FFFFFF",
              bgcolor: "rgba(255,255,255,0.08)",
              borderLeftColor: tokens.rose,
            },
          }}
        >
          <ListItemText primary={item.label} primaryTypographyProps={{ fontWeight: 500 }} />
        </ListItemButton>
      ))}
    </List>
  );
}

function DrawerContent({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <Box sx={{ height: "100%", bgcolor: tokens.inkDeep, color: "#FFFFFF" }}>
      <Box sx={{ px: 2.5, py: 2.5 }}>
        <Typography component="p" sx={{ fontWeight: 600, fontSize: "1.0625rem" }}>
          CFS Operations
        </Typography>
        <Typography component="p" sx={{ fontSize: "0.8125rem", color: "rgba(255,255,255,0.6)" }}>
          Core Financial Services
        </Typography>
      </Box>
      <Divider sx={{ borderColor: "rgba(255,255,255,0.12)", mb: 1 }} />
      <NavList onNavigate={onNavigate} />
    </Box>
  );
}

export function AppLayout() {
  const theme = useTheme();
  const isDesktop = useMediaQuery(theme.breakpoints.up("md"));
  const [open, setOpen] = useState(false);
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function handleSignOut() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <Box sx={{ display: "flex", minHeight: "100vh" }}>
      <OverdueRealtimeRefresh />
      <Drawer
        variant={isDesktop ? "permanent" : "temporary"}
        open={isDesktop || open}
        onClose={() => setOpen(false)}
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          "& .MuiDrawer-paper": {
            width: DRAWER_WIDTH,
            border: 0,
            height: "100vh",
            overflowY: "auto",
            overflowX: "hidden",
            boxSizing: "border-box",
          },
        }}
      >
        <DrawerContent onNavigate={() => setOpen(false)} />
      </Drawer>

      <Box sx={{ flexGrow: 1, minWidth: 0 }}>
        <AppBar position="sticky" sx={{ borderBottom: `1px solid ${tokens.line}` }}>
          <Toolbar sx={{ gap: 2 }}>
            {!isDesktop && (
              <IconButton edge="start" aria-label="Open navigation" onClick={() => setOpen(true)}>
                <MenuIcon />
              </IconButton>
            )}
            <Box sx={{ flexGrow: 1 }} />
            <NotificationBell />
            <Typography variant="body2" color="text.secondary" noWrap>
              {user?.full_name || user?.email}
            </Typography>
            <Button variant="outlined" size="small" onClick={handleSignOut}>
              Sign out
            </Button>
          </Toolbar>
        </AppBar>
        <Box component="main" sx={{ p: { xs: 2, md: 4 }, maxWidth: 1200 }}>
          <Outlet />
        </Box>
      </Box>
    </Box>
  );
}

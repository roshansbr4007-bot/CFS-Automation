import { Box, Chip, Paper, Stack, Typography } from "@mui/material";

import { useAuth } from "../../app/AuthProvider";
import { ChangePasswordForm } from "./ChangePasswordForm";

export function ProfilePage() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <Stack spacing={3}>
      <Typography variant="h2" component="h1">
        My profile
      </Typography>
      <Paper sx={{ p: 3 }}>
        <Box component="dl" sx={{ m: 0, display: "grid", gridTemplateColumns: "max-content 1fr", gap: 1.5 }}>
          <Typography component="dt" color="text.secondary">Name</Typography>
          <Typography component="dd" sx={{ m: 0 }}>{user.full_name || "Not set"}</Typography>
          <Typography component="dt" color="text.secondary">Email</Typography>
          <Typography component="dd" sx={{ m: 0 }}>{user.email}</Typography>
          <Typography component="dt" color="text.secondary">Roles</Typography>
          <Box component="dd" sx={{ m: 0, display: "flex", gap: 1, flexWrap: "wrap" }}>
            {user.roles.length ? user.roles.map((role) => <Chip key={role} label={role} size="small" />) : "No role"}
          </Box>
        </Box>
      </Paper>
      <Paper sx={{ p: 3 }}>
        <Typography variant="h3" component="h2" sx={{ mb: 2 }}>
          Change password
        </Typography>
        <ChangePasswordForm />
      </Paper>
    </Stack>
  );
}

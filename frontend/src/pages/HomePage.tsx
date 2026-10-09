import { Chip, Paper, Stack, Typography } from "@mui/material";

import { useAuth } from "../app/AuthProvider";

export function HomePage() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <Stack spacing={3}>
      <Typography variant="h2" component="h1">
        Welcome, {user.first_name || user.email}
      </Typography>
      <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
        {user.roles.map((role) => (
          <Chip key={role} label={role} variant="outlined" />
        ))}
      </Stack>
      <Paper sx={{ p: 3, maxWidth: 640 }}>
        <Typography>
          This release covers sign-in, user management, departments, employees, daily login history and the audit log. Task and SLA modules will be added in the next phases.
        </Typography>
      </Paper>
    </Stack>
  );
}

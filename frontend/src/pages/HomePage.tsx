import { Chip, Stack, Typography } from "@mui/material";

import { useAuth } from "../app/AuthProvider";
import { MyPerformance } from "../features/performance/MyPerformance";

/** Home: a welcome, the signed-in roles and (Phase 7.4) my own KRA performance, for every role
 * whose login is linked to an employee record. */
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
      <MyPerformance />
    </Stack>
  );
}

import { Button, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";

export function ForbiddenPage() {
  return (
    <Stack spacing={2} sx={{ maxWidth: 520 }}>
      <Typography variant="h2" component="h1">
        You don't have access to this page
      </Typography>
      <Typography color="text.secondary">
        Your role doesn't include this area. Ask an Admin if you need access.
      </Typography>
      <div>
        <Button component={RouterLink} to="/" variant="outlined">
          Go to home
        </Button>
      </div>
    </Stack>
  );
}

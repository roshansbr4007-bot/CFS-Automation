import { Button, Stack, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";

export function NotFoundPage() {
  return (
    <Stack spacing={2} sx={{ maxWidth: 520 }}>
      <Typography variant="h2" component="h1">
        Page not found
      </Typography>
      <Typography color="text.secondary">Check the address, or go back to the home page.</Typography>
      <div>
        <Button component={RouterLink} to="/" variant="outlined">
          Go to home
        </Button>
      </div>
    </Stack>
  );
}

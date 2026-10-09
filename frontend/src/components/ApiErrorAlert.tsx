import { Alert } from "@mui/material";

import { ApiError } from "../api/apiClient";

/** Shows the API's message for errors that are not tied to one form field. */
export function ApiErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const message =
    error instanceof ApiError ? error.message : "Something went wrong. Try again.";
  return (
    <Alert severity="error" role="alert" sx={{ mb: 2 }}>
      {message}
    </Alert>
  );
}

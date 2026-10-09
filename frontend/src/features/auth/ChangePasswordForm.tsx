import { Alert, Button, Stack, TextField } from "@mui/material";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { authApi } from "../../api/endpoints";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { applyApiFieldErrors } from "../../components/formErrors";

interface FormValues {
  current_password: string;
  new_password: string;
  confirm_password: string;
}

const API_FIELDS = ["current_password", "new_password"] as const;

export function ChangePasswordForm() {
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const {
    register,
    handleSubmit,
    reset,
    setError: setFieldError,
    getValues,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    defaultValues: { current_password: "", new_password: "", confirm_password: "" },
  });

  const onSubmit = handleSubmit(async (values) => {
    setError(null);
    setSaved(false);
    try {
      await authApi.changePassword(values.current_password, values.new_password);
      reset();
      setSaved(true);
    } catch (err) {
      if (!applyApiFieldErrors(err, setFieldError, API_FIELDS)) setError(err);
    }
  });

  return (
    <form noValidate onSubmit={onSubmit}>
      {saved && (
        <Alert severity="success" sx={{ mb: 2 }}>
          Password changed.
        </Alert>
      )}
      <ApiErrorAlert error={error} />
      <Stack spacing={2} sx={{ maxWidth: 400 }}>
        <TextField
          label="Current password"
          type="password"
          autoComplete="current-password"
          error={!!errors.current_password}
          helperText={errors.current_password?.message}
          {...register("current_password", { required: "Enter your current password." })}
        />
        <TextField
          label="New password"
          type="password"
          autoComplete="new-password"
          error={!!errors.new_password}
          helperText={errors.new_password?.message}
          {...register("new_password", { required: "Enter a new password." })}
        />
        <TextField
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          error={!!errors.confirm_password}
          helperText={errors.confirm_password?.message}
          {...register("confirm_password", {
            validate: (value) => value === getValues("new_password") || "Passwords do not match.",
          })}
        />
        <div>
          <Button type="submit" variant="contained" disabled={isSubmitting}>
            Change password
          </Button>
        </div>
      </Stack>
    </form>
  );
}

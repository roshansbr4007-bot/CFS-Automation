import { Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { usersApi } from "../../api/endpoints";
import type { User } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { applyApiFieldErrors } from "../../components/formErrors";

interface FormValues {
  password: string;
}

export function SetPasswordDialog({ user, onClose }: { user: User | null; onClose: () => void }) {
  const [error, setError] = useState<unknown>(null);
  const {
    register,
    handleSubmit,
    reset,
    setError: setFieldError,
    formState: { errors },
  } = useForm<FormValues>({ defaultValues: { password: "" } });

  const mutation = useMutation({
    mutationFn: (values: FormValues) => usersApi.setPassword(user!.id, values.password),
    onSuccess: () => {
      reset();
      onClose();
    },
    onError: (err) => {
      if (!applyApiFieldErrors(err, setFieldError, ["password"] as const)) setError(err);
    },
  });

  return (
    <Dialog open={!!user} onClose={onClose} fullWidth maxWidth="xs" aria-labelledby="set-password-title">
      <form noValidate onSubmit={handleSubmit((values) => mutation.mutate(values))}>
        <DialogTitle id="set-password-title">Set password for {user?.email}</DialogTitle>
        <DialogContent>
          <ApiErrorAlert error={error} />
          <TextField
            sx={{ mt: 1 }}
            label="New password"
            type="password"
            autoComplete="new-password"
            error={!!errors.password}
            helperText={errors.password?.message}
            {...register("password", { required: "Enter a password." })}
          />
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={mutation.isPending}>
            Set password
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

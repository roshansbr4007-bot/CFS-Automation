import {
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  FormGroup,
  FormHelperText,
  FormLabel,
  Stack,
  Switch,
  TextField,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";

import { usersApi } from "../../api/endpoints";
import { ROLE_NAMES, type RoleName, type User } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { applyApiFieldErrors } from "../../components/formErrors";

interface FormValues {
  email: string;
  first_name: string;
  last_name: string;
  roles: RoleName[];
  password: string;
  is_active: boolean;
}

const API_FIELDS = ["email", "first_name", "last_name", "roles", "password", "is_active"] as const;

interface Props {
  open: boolean;
  user?: User | null; // absent = create
  onClose: () => void;
  onSetPassword?: (user: User) => void;
}

export function UserFormDialog({ open, user, onClose, onSetPassword }: Props) {
  const isEdit = !!user;
  const queryClient = useQueryClient();
  const [error, setError] = useState<unknown>(null);
  const {
    register,
    control,
    handleSubmit,
    reset,
    setError: setFieldError,
    formState: { errors },
  } = useForm<FormValues>({
    defaultValues: { email: "", first_name: "", last_name: "", roles: [], password: "", is_active: true },
  });

  // Load the selected user (or blank values) each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setError(null);
    reset({
      email: user?.email ?? "",
      first_name: user?.first_name ?? "",
      last_name: user?.last_name ?? "",
      roles: user?.roles ?? [],
      password: "",
      is_active: user?.is_active ?? true,
    });
  }, [open, user, reset]);

  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      isEdit
        ? usersApi.update(user!.id, {
            first_name: values.first_name,
            last_name: values.last_name,
            roles: values.roles,
            is_active: values.is_active,
          })
        : usersApi.create({
            email: values.email,
            first_name: values.first_name,
            last_name: values.last_name,
            roles: values.roles,
            password: values.password,
          }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
    onError: (err) => {
      if (!applyApiFieldErrors(err, setFieldError, API_FIELDS)) setError(err);
    },
  });

  const onSubmit = handleSubmit((values) => {
    setError(null);
    mutation.mutate(values);
  });

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="user-dialog-title">
      <form noValidate onSubmit={onSubmit}>
        <DialogTitle id="user-dialog-title">{isEdit ? "Edit user" : "Add user"}</DialogTitle>
        <DialogContent>
          <ApiErrorAlert error={error} />
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField
              label="Email"
              type="email"
              disabled={isEdit}
              error={!!errors.email}
              helperText={errors.email?.message ?? (isEdit ? "Email cannot be changed." : undefined)}
              {...register("email", { required: !isEdit && "Enter an email address." })}
            />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                label="First name"
                error={!!errors.first_name}
                helperText={errors.first_name?.message}
                {...register("first_name")}
              />
              <TextField
                label="Last name"
                error={!!errors.last_name}
                helperText={errors.last_name?.message}
                {...register("last_name")}
              />
            </Stack>
            <Controller
              name="roles"
              control={control}
              render={({ field }) => (
                <FormControl error={!!errors.roles} component="fieldset">
                  <FormLabel component="legend">Roles</FormLabel>
                  <FormGroup row>
                    {ROLE_NAMES.map((role) => (
                      <FormControlLabel
                        key={role}
                        label={role}
                        control={
                          <Checkbox
                            checked={field.value.includes(role)}
                            onChange={(event) =>
                              field.onChange(
                                event.target.checked
                                  ? [...field.value, role]
                                  : field.value.filter((r) => r !== role),
                              )
                            }
                          />
                        }
                      />
                    ))}
                  </FormGroup>
                  {errors.roles && <FormHelperText>{errors.roles.message}</FormHelperText>}
                </FormControl>
              )}
            />
            {!isEdit && (
              <TextField
                label="Password"
                type="password"
                autoComplete="new-password"
                error={!!errors.password}
                helperText={errors.password?.message ?? "Share it with the person securely."}
                {...register("password", { required: "Enter a password." })}
              />
            )}
            {isEdit && (
              <Controller
                name="is_active"
                control={control}
                render={({ field }) => (
                  <FormControlLabel
                    label={field.value ? "Active: can sign in" : "Deactivated: cannot sign in"}
                    control={
                      <Switch checked={field.value} onChange={(e) => field.onChange(e.target.checked)} />
                    }
                  />
                )}
              />
            )}
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          {isEdit && onSetPassword && (
            <Button onClick={() => onSetPassword(user!)} sx={{ mr: "auto" }}>
              Set password
            </Button>
          )}
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={mutation.isPending}>
            {isEdit ? "Save changes" : "Add user"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

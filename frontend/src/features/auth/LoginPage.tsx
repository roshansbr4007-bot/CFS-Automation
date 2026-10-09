import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../../app/AuthProvider";
import { tokens } from "../../app/theme";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";

interface FormValues {
  email: string;
  password: string;
}

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState<unknown>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ defaultValues: { email: "", password: "" } });

  const from = (location.state as { from?: string } | null)?.from ?? "/";
  if (user) return <Navigate to={from} replace />;

  const onSubmit = handleSubmit(async (values) => {
    setError(null);
    try {
      await login(values.email, values.password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err);
    }
  });

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "minmax(320px, 5fr) 7fr" },
      }}
    >
      <Box
        sx={{
          bgcolor: tokens.inkDeep,
          color: "#FFFFFF",
          px: { xs: 3, md: 6 },
          py: { xs: 4, md: 8 },
          display: "flex",
          flexDirection: "column",
          justifyContent: { md: "flex-end" },
          borderBottom: { xs: `4px solid ${tokens.rose}`, md: "none" },
          borderRight: { md: `4px solid ${tokens.rose}` },
        }}
      >
        <Typography variant="h1" component="p" sx={{ fontSize: { xs: "1.75rem", md: "2.5rem" } }}>
          CFS Operations
        </Typography>
        <Typography sx={{ mt: 1.5, maxWidth: 380, color: "rgba(255,255,255,0.75)" }}>
          Tasks, deadlines and accountability for the Operations team at Core Financial Services.
        </Typography>
      </Box>

      <Box sx={{ display: "grid", placeItems: "center", px: 3, py: 6 }}>
        <Box component="form" noValidate onSubmit={onSubmit} sx={{ width: "100%", maxWidth: 380 }}>
          <Typography variant="h2" component="h1" sx={{ mb: 3 }}>
            Sign in
          </Typography>
          <ApiErrorAlert error={error} />
          <Stack spacing={2}>
            <TextField
              label="Email"
              type="email"
              autoComplete="email"
              autoFocus
              error={!!errors.email}
              helperText={errors.email?.message}
              {...register("email", { required: "Enter your email." })}
            />
            <TextField
              label="Password"
              type="password"
              autoComplete="current-password"
              error={!!errors.password}
              helperText={errors.password?.message}
              {...register("password", { required: "Enter your password." })}
            />
            <Button type="submit" variant="contained" size="large" disabled={isSubmitting}>
              {isSubmitting ? "Signing in…" : "Sign in"}
            </Button>
          </Stack>
        </Box>
      </Box>
    </Box>
  );
}

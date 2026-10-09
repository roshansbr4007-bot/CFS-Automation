import { Button, Chip, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { usersApi } from "../../api/endpoints";
import { ROLE_NAMES, type User } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { DateTimeText } from "../../components/DateTimeText";
import { SetPasswordDialog } from "./SetPasswordDialog";
import { UserFormDialog } from "./UserFormDialog";

export function UsersPage() {
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [dialog, setDialog] = useState<{ open: boolean; user: User | null }>({ open: false, user: null });
  const [passwordUser, setPasswordUser] = useState<User | null>(null);

  const params = { page: page + 1, search, role, is_active: status };
  const { data, isFetching, error } = useQuery({
    queryKey: ["users", params],
    queryFn: () => usersApi.list(params),
    placeholderData: keepPreviousData,
  });

  const columns: Column<User>[] = [
    { key: "name", header: "Name", render: (u) => u.full_name || "—" },
    { key: "email", header: "Email", render: (u) => u.email },
    {
      key: "roles",
      header: "Roles",
      render: (u) => (
        <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap" }}>
          {u.roles.map((r) => (
            <Chip key={r} label={r} size="small" />
          ))}
        </Stack>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (u) => (u.is_active ? "Active" : "Deactivated"),
    },
    { key: "last_login", header: "Last sign-in", render: (u) => <DateTimeText value={u.last_login} /> },
    {
      key: "actions",
      header: "",
      render: (u) => (
        <Button size="small" onClick={() => setDialog({ open: true, user: u })}>
          Edit
        </Button>
      ),
    },
  ];

  const resetPage = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value);
    setPage(0);
  };

  return (
    <Stack spacing={3}>
      <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between" }}>
        <Typography variant="h2" component="h1">
          Users
        </Typography>
        <Button variant="contained" onClick={() => setDialog({ open: true, user: null })}>
          Add user
        </Button>
      </Stack>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
        <TextField
          label="Search name or email"
          value={search}
          onChange={(e) => resetPage(setSearch)(e.target.value)}
        />
        <TextField select label="Role" value={role} onChange={(e) => resetPage(setRole)(e.target.value)}>
          <MenuItem value="">All roles</MenuItem>
          {ROLE_NAMES.map((r) => (
            <MenuItem key={r} value={r}>
              {r}
            </MenuItem>
          ))}
        </TextField>
        <TextField select label="Status" value={status} onChange={(e) => resetPage(setStatus)(e.target.value)}>
          <MenuItem value="">All</MenuItem>
          <MenuItem value="true">Active</MenuItem>
          <MenuItem value="false">Deactivated</MenuItem>
        </TextField>
      </Stack>

      <ApiErrorAlert error={error} />
      <DataTable
        caption="Users"
        columns={columns}
        rows={data?.results ?? []}
        getRowId={(u) => u.id}
        loading={isFetching}
        total={data?.count ?? 0}
        page={page}
        onPageChange={setPage}
        emptyMessage="No users match these filters."
      />

      <UserFormDialog
        open={dialog.open}
        user={dialog.user}
        onClose={() => setDialog({ open: false, user: null })}
        onSetPassword={(u) => setPasswordUser(u)}
      />
      <SetPasswordDialog user={passwordUser} onClose={() => setPasswordUser(null)} />
    </Stack>
  );
}

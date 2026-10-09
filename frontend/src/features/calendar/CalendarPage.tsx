import {
  Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField, Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "../../api/apiClient";
import { calendarApi } from "../../api/endpoints";
import type { CalendarDay, CalendarDayKind } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DataTable, type Column } from "../../components/DataTable";
import { formatBusinessDate } from "../../components/DateTimeText";

const KIND_LABEL: Record<CalendarDayKind, string> = { HOLIDAY: "Holiday", SPECIAL_WORKING_DAY: "Special working day" };

/** Company Calendar: the weekly rule is fixed by policy; Admin adds holidays and special days. */
export function CalendarPage() {
  const qc = useQueryClient();
  const { data, isFetching, error } = useQuery({ queryKey: ["company-calendar"], queryFn: calendarApi.get });
  const [adding, setAdding] = useState(false);
  const remove = useMutation({
    mutationFn: (id: number) => calendarApi.removeDay(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["company-calendar"] }),
  });
  const columns: Column<CalendarDay>[] = [
    { key: "date", header: "Date", render: (d) => formatBusinessDate(d.date) },
    { key: "kind", header: "Type", render: (d) => <Chip size="small" label={KIND_LABEL[d.kind]} color={d.kind === "HOLIDAY" ? "warning" : "info"} /> },
    { key: "name", header: "Name", render: (d) => d.name },
    { key: "remove", header: "", render: (d) => <Button size="small" color="error" disabled={remove.isPending} onClick={() => remove.mutate(d.id)}>Remove</Button> },
  ];
  return (
    <Stack spacing={3}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Typography variant="h2" component="h1">Company calendar</Typography>
        <Button variant="contained" onClick={() => setAdding(true)}>Add day</Button>
      </Stack>
      <Alert severity="info">
        Working days: Monday to Friday, and the 1st and 3rd Saturday. Sundays and the 2nd, 4th and 5th Saturdays are off.
        Holidays below are off; special working days are on.
      </Alert>
      <ApiErrorAlert error={error ?? remove.error} />
      <DataTable caption="Holidays and special working days" columns={columns} rows={data?.days ?? []} getRowId={(d) => d.id}
        loading={isFetching} total={data?.days.length ?? 0} page={0} onPageChange={() => undefined} emptyMessage="No holidays or special working days yet." />
      <AddDayDialog open={adding} onClose={() => setAdding(false)} />
    </Stack>
  );
}

function AddDayDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [date, setDate] = useState(""); const [kind, setKind] = useState<CalendarDayKind>("HOLIDAY"); const [name, setName] = useState("");
  const mutation = useMutation({
    mutationFn: () => calendarApi.addDay({ date, kind, name }),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["company-calendar"] }); setDate(""); setName(""); onClose(); },
  });
  const apiError = mutation.error instanceof ApiError ? mutation.error : null;
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Add holiday or special working day</DialogTitle>
      <DialogContent><Stack spacing={2} sx={{ mt: 1 }}>
        {apiError && <Alert severity="error">{apiError.message}</Alert>}
        <TextField size="small" label="Date" type="date" value={date} onChange={(e) => setDate(e.target.value)} InputLabelProps={{ shrink: true }} />
        <TextField size="small" select label="Type" value={kind} onChange={(e) => setKind(e.target.value as CalendarDayKind)}>
          {(Object.keys(KIND_LABEL) as CalendarDayKind[]).map((k) => <MenuItem key={k} value={k}>{KIND_LABEL[k]}</MenuItem>)}
        </TextField>
        <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} />
      </Stack></DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!date || !name.trim() || mutation.isPending} onClick={() => mutation.mutate()}>Add</Button>
      </DialogActions>
    </Dialog>
  );
}

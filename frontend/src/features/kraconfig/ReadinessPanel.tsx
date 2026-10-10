/** Phase 7.5A: "would Admin's activation succeed right now?" - shown exactly as the backend's
 * read-only readiness check answers it (the same problems activation would refuse with). The
 * screen never works out readiness itself; activation re-checks everything on the server. */
import { Alert, AlertTitle, LinearProgress, Paper, Stack, Typography } from "@mui/material";

import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { formatBusinessDate } from "../../components/DateTimeText";
import { useReadiness } from "./api";

export function ReadinessPanel({ planId }: { planId: number }) {
  const { data, error, isFetching } = useReadiness(planId);
  return (
    <Paper component="section" aria-label="Activation readiness" sx={{ p: 2 }}>
      <Stack spacing={1.5}>
        <Typography variant="h6" component="h2">Activation readiness</Typography>
        {isFetching && <LinearProgress aria-label="Checking readiness" />}
        <ApiErrorAlert error={error} />
        {data && data.ready && (
          <Alert severity="success">
            <AlertTitle>Ready to activate</AlertTitle>
            Nothing would stop Admin from activating this draft (checked for {formatBusinessDate(data.checked_on)}).
          </Alert>
        )}
        {data && !data.ready && (
          <Alert severity={data.status === "DRAFT" ? "warning" : "info"}>
            <AlertTitle>{data.status === "DRAFT" ? "Not ready to activate yet" : "Not a draft"}</AlertTitle>
            <ul aria-label="Readiness problems" style={{ margin: 0, paddingLeft: 20 }}>
              {data.problems.map((problem) => <li key={problem}>{problem}</li>)}
            </ul>
            <Typography variant="caption" component="p" sx={{ mt: 1 }}>
              Checked for {formatBusinessDate(data.checked_on)}. Activation checks again when Admin activates.
            </Typography>
          </Alert>
        )}
      </Stack>
    </Paper>
  );
}

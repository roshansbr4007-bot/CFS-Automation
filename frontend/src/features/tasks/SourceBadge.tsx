import { Chip } from "@mui/material";

import type { TaskSource } from "../../api/types";

/** Makes it obvious whether the system generated the task or a person assigned it. */
export function SourceBadge({ source }: { source: TaskSource }) {
  return source === "SCHEDULED"
    ? <Chip size="small" color="info" label="SCHEDULED" />
    : <Chip size="small" variant="outlined" label="MANUAL" />;
}

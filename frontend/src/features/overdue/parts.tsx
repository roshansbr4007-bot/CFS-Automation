import { Card, CardContent, Chip, Stack, Typography } from "@mui/material";
import type { ReactNode } from "react";

import type { OverdueStatus } from "../../api/types";
import { OVERDUE_STATUS_LABEL } from "./labels";

/** Same presentation as the Task detail page's private Row / Panel (not shared there). */
export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Stack direction="row" spacing={1} sx={{ py: 0.4 }}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 150 }}>{label}</Typography>
      <Typography variant="body2" component="div" sx={{ whiteSpace: "pre-wrap" }}>{children}</Typography>
    </Stack>
  );
}

export function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card variant="outlined" component="section" aria-label={title}>
      <CardContent>
        <Typography variant="overline" color="text.secondary" component="h2">{title}</Typography>
        {children}
      </CardContent>
    </Card>
  );
}

const STATUS_COLOR: Record<OverdueStatus, "warning" | "info" | "success"> = {
  OPEN: "warning",
  REASON_SUBMITTED: "info",
  REVIEWED: "success",
};

export function OverdueStatusChip({ status }: { status: OverdueStatus }) {
  return <Chip size="small" color={STATUS_COLOR[status]} label={OVERDUE_STATUS_LABEL[status]} />;
}

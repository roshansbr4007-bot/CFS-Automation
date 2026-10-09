import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import Grid from "@mui/material/Grid2";
import { useState } from "react";
import { Link as RouterLink, useParams } from "react-router-dom";

import type { OverdueCase } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { DateTimeText } from "../../components/DateTimeText";
import { PRIORITY_LABEL } from "../tasks/labels";
import { formatOverdueMinutes, OPENED_VIA_LABEL, OVERDUE_CAUSE_LABEL } from "./labels";
import { ReasonForm, ReviewForm } from "./OverdueForms";
import { OverdueStatusChip, Panel, Row } from "./parts";
import { useOverdueCase } from "./queries";

/** The one plain-language line: why the case exists and whether anyone still has to act. */
function statusMessage(c: OverdueCase): { severity: "warning" | "info" | "success"; text: string } {
  if (c.status === "REVIEWED") return { severity: "success", text: "Reviewed. This case is closed and can no longer change." };
  if (c.status === "REASON_SUBMITTED") {
    return c.can_review
      ? { severity: "warning", text: "The employee has submitted a reason. Your review is needed." }
      : { severity: "info", text: "The reason has been submitted and is waiting for review." };
  }
  return c.can_submit
    ? { severity: "warning", text: "This task passed its deadline while assigned to you. Please submit the reason." }
    : { severity: "info", text: `Waiting for ${c.employee.full_name} to submit the reason.` };
}

export function OverdueCaseDetailPage() {
  const id = Number(useParams().id);
  const valid = Number.isInteger(id) && id > 0;
  const query = useOverdueCase(id);
  const [notice, setNotice] = useState<string | null>(null);

  if (!valid) return <Alert severity="error" role="alert">This overdue case does not exist.</Alert>;
  if (query.isError) {
    return (
      <Stack spacing={1} sx={{ alignItems: "flex-start" }}>
        <ApiErrorAlert error={query.error} />
        <Button component={RouterLink} to="/overdue-cases">Back to my overdue cases</Button>
      </Stack>
    );
  }
  const c = query.data;
  if (!c) return <Typography>Loading…</Typography>;
  const message = statusMessage(c);
  const reviewed = c.status === "REVIEWED";

  return (
    <Stack spacing={2.5}>
      <Box>
        <Typography variant="overline" color="text.secondary">Overdue case #{c.id} · {c.task_reference}</Typography>
        <Typography variant="h2" component="h1">{c.task_title}</Typography>
        <Stack direction="row" spacing={1} sx={{ mt: 1, alignItems: "center", flexWrap: "wrap" }}>
          <OverdueStatusChip status={c.status} />
          <Typography variant="body2" color="text.secondary">
            Opened <DateTimeText value={c.opened_at} /> by the {OPENED_VIA_LABEL[c.opened_via].toLowerCase()}
          </Typography>
        </Stack>
      </Box>
      {notice && <Alert severity="success" onClose={() => setNotice(null)}>{notice}</Alert>}
      <Alert severity={message.severity}>{message.text}</Alert>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Stack spacing={2}>
            <Panel title="Task">
              <Row label="Task"><RouterLink to={`/tasks/${c.task_id}`}>{c.task_reference}</RouterLink> {c.task_title}</Row>
              <Row label="Employee">{c.employee.full_name}{c.employee.employee_code ? ` (${c.employee.employee_code})` : ""}</Row>
              <Row label="Department">{c.department.code} — {c.department.name}</Row>
              <Row label="Category">{c.category_name || "—"}</Row>
              <Row label="Priority">{PRIORITY_LABEL[c.priority]}</Row>
              <Row label="Raised by">{c.task_creator.email}</Row>
              <Row label="Assigned at"><DateTimeText value={c.task_assigned_at} /></Row>
            </Panel>
            <Panel title="Overdue">
              <Row label="SLA rule">{c.sla_rule_name} ({c.sla_rule_code})</Row>
              <Row label="SLA start"><DateTimeText value={c.sla_start_at} /></Row>
              <Row label="Deadline"><DateTimeText value={c.sla_due_at} /></Row>
              <Row label="Overdue since"><DateTimeText value={c.overdue_at} /></Row>
              <Row label="Completed at"><DateTimeText value={c.completed_at} /></Row>
              <Row label="Overdue for">
                {formatOverdueMinutes(c.overdue_minutes)}{c.completed_at ? " (until completion)" : " (still counting)"}
              </Row>
              <Row label="Dependency">Not applicable</Row>
            </Panel>
          </Stack>
        </Grid>
        <Grid size={{ xs: 12, md: 6 }}>
          <Stack spacing={2}>
            <Panel title="Employee's reason">
              {c.reason_category ? (
                <>
                  <Row label="Reason category">{OVERDUE_CAUSE_LABEL[c.reason_category]}</Row>
                  <Row label="Explanation">{c.explanation}</Row>
                  <Row label="Submitted at"><DateTimeText value={c.submitted_at} /></Row>
                  <Row label="Submitted by">{c.submitted_by?.email ?? "—"}</Row>
                </>
              ) : (
                <Typography variant="body2" color="text.secondary">Not submitted yet.</Typography>
              )}
              {c.can_submit && <ReasonForm overdueCase={c} onDone={() => setNotice("Your reason was submitted.")} />}
            </Panel>
            <Panel title="Reviewer's decision">
              {reviewed ? (
                <>
                  <Row label="Authoritative cause">{c.cause ? OVERDUE_CAUSE_LABEL[c.cause] : "—"}</Row>
                  <Row label="Review remark">{c.review_remark}</Row>
                  <Row label="Reviewed at"><DateTimeText value={c.reviewed_at} /></Row>
                  <Row label="Reviewed by">{c.reviewed_by?.email ?? "—"}</Row>
                </>
              ) : (
                <Typography variant="body2" color="text.secondary">Not reviewed yet.</Typography>
              )}
              {c.can_review && <ReviewForm overdueCase={c} onDone={() => setNotice("Your review was recorded. The case is now closed.")} />}
            </Panel>
          </Stack>
        </Grid>
      </Grid>
    </Stack>
  );
}

import { Alert, Button, MenuItem, Stack, TextField } from "@mui/material";
import { useState } from "react";

import { ApiError } from "../../api/apiClient";
import { OVERDUE_CAUSES, type OverdueCase, type OverdueCause } from "../../api/types";
import { ApiErrorAlert } from "../../components/ApiErrorAlert";
import { OVERDUE_CAUSE_LABEL } from "./labels";
import { useReviewOverdueCase, useSubmitOverdueReason } from "./queries";

const MAX_TEXT = 4000; // backend limit (SubmitReasonSerializer / ReviewSerializer)

/** Client checks mirror the backend: a required choice and required, non-blank text. */
function required(value: string, label: string): string | undefined {
  if (!value.trim()) return `${label} is required.`;
  if (value.length > MAX_TEXT) return `Use at most ${MAX_TEXT} characters.`;
  return undefined;
}

function serverFieldError(error: unknown, name: string): string | undefined {
  return error instanceof ApiError ? error.fields?.[name]?.join(" ") : undefined;
}

/** Errors that belong to no field (403, 409, …) are shown above the form. */
function formLevelError(error: unknown, fields: string[]): unknown {
  if (!(error instanceof ApiError)) return error;
  return fields.some((f) => error.fields?.[f]) ? null : error;
}

function CausePicker({ label, value, onChange, error }: {
  label: string; value: OverdueCause | ""; onChange: (v: OverdueCause) => void; error?: string;
}) {
  return (
    <TextField select size="small" label={label} required value={value} error={!!error} helperText={error}
      onChange={(e) => onChange(e.target.value as OverdueCause)} sx={{ maxWidth: 320 }}>
      {OVERDUE_CAUSES.map((c) => <MenuItem key={c} value={c}>{OVERDUE_CAUSE_LABEL[c]}</MenuItem>)}
    </TextField>
  );
}

/** The employee on the case submits the reason once (shown only when the server allows it). */
export function ReasonForm({ overdueCase, onDone }: { overdueCase: OverdueCase; onDone: () => void }) {
  const submit = useSubmitOverdueReason(overdueCase.id);
  const [category, setCategory] = useState<OverdueCause | "">("");
  const [explanation, setExplanation] = useState("");
  const [checked, setChecked] = useState(false);
  const categoryError = checked && !category ? "Choose a reason category." : undefined;
  const explanationError = checked ? required(explanation, "An explanation") : undefined;

  const send = () => {
    setChecked(true);
    if (!category || required(explanation, "An explanation")) return;
    // mutateAsync: the form unmounts once the server says it is submitted; the parent's notice must not depend on it.
    submit.mutateAsync({ version: overdueCase.version, reason_category: category, explanation }).then(onDone, () => undefined);
  };

  return (
    <Stack component="form" aria-label="Submit your reason" spacing={1.5} sx={{ mt: 1 }} noValidate
      onSubmit={(e) => { e.preventDefault(); send(); }}>
      <ApiErrorAlert error={formLevelError(submit.error, ["reason_category", "explanation", "version"])} />
      <CausePicker label="Reason category" value={category} onChange={setCategory}
        error={categoryError ?? serverFieldError(submit.error, "reason_category")} />
      <TextField label="Explanation" required multiline minRows={3} value={explanation}
        onChange={(e) => setExplanation(e.target.value)} inputProps={{ maxLength: MAX_TEXT }}
        error={!!(explanationError ?? serverFieldError(submit.error, "explanation"))}
        helperText={explanationError ?? serverFieldError(submit.error, "explanation") ?? "Explain what delayed the task. You can submit once."} />
      <div><Button type="submit" variant="contained" disabled={submit.isPending}>Submit reason</Button></div>
    </Stack>
  );
}

/** An eligible reviewer records the authoritative cause (shown only when the server allows it). */
export function ReviewForm({ overdueCase, onDone }: { overdueCase: OverdueCase; onDone: () => void }) {
  const review = useReviewOverdueCase(overdueCase.id);
  const [cause, setCause] = useState<OverdueCause | "">("");
  const [remark, setRemark] = useState("");
  const [checked, setChecked] = useState(false);
  const causeError = checked && !cause ? "Choose the authoritative cause." : undefined;
  const remarkError = checked ? required(remark, "A review remark") : undefined;

  const send = () => {
    setChecked(true);
    if (!cause || required(remark, "A review remark")) return;
    review.mutateAsync({ version: overdueCase.version, cause, remark }).then(onDone, () => undefined); // error stays on the mutation
  };

  return (
    <Stack component="form" aria-label="Record your review" spacing={1.5} sx={{ mt: 1 }} noValidate
      onSubmit={(e) => { e.preventDefault(); send(); }}>
      <ApiErrorAlert error={formLevelError(review.error, ["cause", "remark", "version"])} />
      <Alert severity="info">Your decision is final: a reviewed case can no longer change.</Alert>
      <CausePicker label="Authoritative cause" value={cause} onChange={setCause}
        error={causeError ?? serverFieldError(review.error, "cause")} />
      <TextField label="Review remark" required multiline minRows={3} value={remark}
        onChange={(e) => setRemark(e.target.value)} inputProps={{ maxLength: MAX_TEXT }}
        error={!!(remarkError ?? serverFieldError(review.error, "remark"))}
        helperText={remarkError ?? serverFieldError(review.error, "remark")} />
      <div><Button type="submit" variant="contained" disabled={review.isPending}>Record review</Button></div>
    </Stack>
  );
}

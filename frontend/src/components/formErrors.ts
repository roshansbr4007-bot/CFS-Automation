import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

import { ApiError } from "../api/apiClient";

/**
 * Puts API field errors next to the matching form fields.
 * Returns true when at least one error was placed on a field.
 */
export function applyApiFieldErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fieldNames: readonly Path<T>[],
): boolean {
  if (!(error instanceof ApiError)) return false;
  let placed = false;
  for (const [field, messages] of Object.entries(error.fields)) {
    if ((fieldNames as readonly string[]).includes(field)) {
      setError(field as Path<T>, { type: "server", message: messages.join(" ") });
      placed = true;
    }
  }
  return placed;
}

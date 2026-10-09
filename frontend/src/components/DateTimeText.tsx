const formatter = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

export function formatIST(value: string): string {
  return formatter.format(new Date(value));
}

const dateFormatter = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });

const timeFormatter = new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });

/** "10:00" (24-hour IST) from a server timestamp. Display only. */
export function formatTimeIST(value: string): string {
  return timeFormatter.format(new Date(value));
}

/** A business date from the server (YYYY-MM-DD, already an IST date), shown as "05 Oct 2026". */
export function formatBusinessDate(value: string): string {
  return dateFormatter.format(new Date(`${value}T12:00:00+05:30`));
}

/** Displays a server timestamp in IST. It formats only; it never calculates dates. */
export function DateTimeText({ value }: { value: string | null | undefined }) {
  if (!value) return <span>—</span>;
  return <time dateTime={value}>{formatIST(value)}</time>;
}

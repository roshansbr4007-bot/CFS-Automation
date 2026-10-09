import { Chip, Stack, Typography } from "@mui/material";
import { useEffect, useState } from "react";

/** "1h 00m remaining", "30m remaining", "00m remaining". Pure formatting of a number of seconds. */
export function formatRemaining(seconds: number): string {
  const safe = Math.max(0, seconds);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const mm = String(minutes).padStart(2, "0");
  return hours > 0 ? `${hours}h ${mm}m remaining` : `${mm}m remaining`;
}

/** Live countdown to a backend deadline.
 * `remainingSeconds` is the server's own figure at response time and `receivedAt` the moment that
 * response arrived (ms); only the time elapsed since then is measured locally, so the browser's
 * clock and timezone never decide a deadline. The backend's next refresh stays authoritative. */
export function Countdown({ remainingSeconds, receivedAt }: { remainingSeconds: number; receivedAt: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const left = remainingSeconds - Math.floor(Math.max(0, now - receivedAt) / 1000);
  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
      <Typography variant="body2">{formatRemaining(left)}</Typography>
      {left <= 0 && <Chip size="small" color="error" label="Overdue" />}
    </Stack>
  );
}

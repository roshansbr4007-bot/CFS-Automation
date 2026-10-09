import { useState } from "react";

/** TablePagination needs the total while the NEXT page loads (its query has no data yet).
 * Returns the current count, else the last known one, so `page` never falls out of range.
 * Uses React's "adjust state while rendering" pattern (no effect, no extra request). */
export function useKnownTotal(count: number | undefined): number {
  const [known, setKnown] = useState(0);
  if (count !== undefined && count !== known) setKnown(count);
  return count ?? known;
}

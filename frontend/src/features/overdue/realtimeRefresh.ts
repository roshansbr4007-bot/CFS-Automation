/** Phase 9 Stage 5.4 (spec section 18): refresh overdue REST data when the backend announces a
 * change over the existing Phase 8 socket. Events are SIGNALS only: nothing in the payload is
 * written to the cache; case_id only selects which cached case to refresh. No new socket. */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import type { RealtimeEvent } from "../../realtime/realtime";
import { useRealtimeEvent } from "../../realtime/RealtimeProvider";
import { overdueKeys } from "./queries";

/** Exactly the events apps/overdue/services.py publishes. */
export const OVERDUE_EVENTS = ["overdue_case.opened", "overdue_case.submitted", "overdue_case.reviewed"] as const;

export function useOverdueRealtimeRefresh(): void {
  const qc = useQueryClient();
  const refresh = useCallback((event: RealtimeEvent) => {
    // My cases, review queues and the report (all under ["overdue-cases", "list"]).
    void qc.invalidateQueries({ queryKey: overdueKeys.lists() });
    const id = event.data.case_id;
    if (typeof id === "number" && Number.isInteger(id) && id > 0) {
      void qc.invalidateQueries({ queryKey: overdueKeys.detail(id) });
    }
  }, [qc]);
  useRealtimeEvent("overdue_case.opened", refresh);
  useRealtimeEvent("overdue_case.submitted", refresh);
  useRealtimeEvent("overdue_case.reviewed", refresh);
}

/** Renders nothing; mount once inside RealtimeProvider (wiring point awaits approval). */
export function OverdueRealtimeRefresh(): null {
  useOverdueRealtimeRefresh();
  return null;
}

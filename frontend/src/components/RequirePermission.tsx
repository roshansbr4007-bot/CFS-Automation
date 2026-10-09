import type { ReactNode } from "react";

import { useAuth } from "../app/AuthProvider";

/** Hides its children unless the user holds `perm`. Convenience only: the API enforces access. */
export function RequirePermission({ perm, children }: { perm: string; children: ReactNode }) {
  const { hasPerm } = useAuth();
  return hasPerm(perm) ? <>{children}</> : null;
}

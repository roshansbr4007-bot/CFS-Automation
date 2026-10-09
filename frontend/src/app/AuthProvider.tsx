import { hashKey, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { ApiError } from "../api/apiClient";
import { authApi } from "../api/endpoints";
import type { Me } from "../api/types";
import { ME_QUERY_KEY as ME_KEY } from "./queryClient";

interface AuthState {
  user: Me | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  hasPerm: (perm: string) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

async function fetchMe(): Promise<Me | null> {
  try {
    return await authApi.me();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  // Changes only on sign-in and sign-out, which update it directly; a 401 anywhere clears it.
  const { data, isLoading } = useQuery({
    queryKey: ME_KEY,
    queryFn: fetchMe,
    retry: false,
    staleTime: Infinity,
  });
  const user = data ?? null;

  const login = useCallback(
    async (email: string, password: string) => {
      const me = await authApi.login(email, password);
      queryClient.setQueryData(ME_KEY, me);
      return me;
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      // Signed-out state FIRST: setQueryData notifies this provider's own "me" observer, so the
      // login page and protected routes see no user at once. (Calling clear() first removed the
      // query without notifying its observer, so the old user stayed in context and the login
      // page sent the user straight back into the app.) Then drop every other cached query so no
      // data of the previous user survives.
      queryClient.setQueryData(ME_KEY, null);
      const meHash = hashKey(ME_KEY);
      queryClient.removeQueries({ predicate: (query) => query.queryHash !== meHash });
    }
  }, [queryClient]);

  const hasPerm = useCallback((perm: string) => !!user?.permissions.includes(perm), [user]);

  const value = useMemo(
    () => ({ user, isLoading, login, logout, hasPerm }),
    [user, isLoading, login, logout, hasPerm],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

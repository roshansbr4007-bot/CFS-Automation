import { QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "../api/apiClient";

export const ME_QUERY_KEY = ["auth", "me"];

export function createQueryClient() {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => {
        // Session ended or expired: treat as signed out; routes then redirect to /login.
        if (error instanceof ApiError && error.status === 401) client.setQueryData(ME_QUERY_KEY, null);
      },
    }),
    defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 30_000 } },
  });
  return client;
}

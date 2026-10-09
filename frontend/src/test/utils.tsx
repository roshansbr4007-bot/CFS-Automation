import { ThemeProvider } from "@mui/material";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";

import { AuthProvider } from "../app/AuthProvider";
import { AppRoutes } from "../app/routes";
import { theme } from "../app/theme";

function providers(children: ReactElement, route: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[route]}>
          <AuthProvider>{children}</AuthProvider>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

export function renderApp(route = "/") {
  return render(providers(<AppRoutes />, route));
}

export function renderWithProviders(ui: ReactElement, route = "/") {
  return render(providers(ui, route));
}

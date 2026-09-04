"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 1_000,
        retry: (count, error) => count < 2 && (!(error instanceof Error) || !("status" in error) || Number(error.status) >= 500),
      },
    },
  }));
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

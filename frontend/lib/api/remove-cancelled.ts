import type { QueryClient } from "@tanstack/react-query";
import type { BatchDto, HistoryDto } from "./types";

export async function removeCancelled(client: QueryClient, ids: string[]) {
  const removed = new Set(ids);
  await client.cancelQueries({ predicate: query => query.queryKey[0] === "history" || query.queryKey[0] === "batch" || query.queryKey[0] === "job" && removed.has(String(query.queryKey[1])) });
  client.setQueriesData<HistoryDto>({ queryKey: ["history"] }, data => data && ({ ...data, jobs: data.jobs.filter(job => !removed.has(job.id)) }));
  client.setQueriesData<BatchDto>({ queryKey: ["batch"] }, data => data && ({ ...data, jobs: data.jobs.filter(job => !removed.has(job.id)) }));
  ids.forEach(id => client.removeQueries({ queryKey: ["job", id], exact: true }));
}

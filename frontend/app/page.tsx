import { Suspense } from "react";
import { Workspace } from "@/components/workspace";

export default function HomePage() {
  return <Suspense fallback={<main className="state-card"><strong>Loading workspace…</strong></main>}><Workspace /></Suspense>;
}

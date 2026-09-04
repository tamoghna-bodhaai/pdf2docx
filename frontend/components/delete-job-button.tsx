"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "@/lib/api/client";
import type { JobDto } from "@/lib/api/types";
import { ConfirmDialog } from "./confirm-dialog";

const RUNNING = new Set(["queued", "rendering", "transcribing", "building", "processing"]);

export function DeleteJobButton({ job, onDeleted }: { job: JobDto; onDeleted: () => void }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const remove = useMutation({
    mutationFn: () => api.deleteJob(job.id),
    onSuccess: () => {
      setConfirming(false);
      queryClient.removeQueries({ queryKey: ["job", job.id] });
      queryClient.invalidateQueries({ queryKey: ["history"] });
      onDeleted();
    },
  });
  const error = remove.error instanceof ApiError
    ? remove.error.message
    : remove.error ? "The file could not be deleted." : "";
  return (
    <>
      <button
        type="button"
        disabled={RUNNING.has(job.status) || remove.isPending}
        onClick={() => setConfirming(true)}
      >
        Delete
      </button>
      {error && <p role="alert">{error}</p>}
      <ConfirmDialog
        open={confirming}
        title={`Delete ${job.output_filename || job.filename}?`}
        description="This removes the source and every stored output. This cannot be undone."
        onClose={() => setConfirming(false)}
        onConfirm={() => remove.mutate()}
      />
    </>
  );
}

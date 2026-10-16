import type { JobStatus } from "../api";

const COLOURS: Record<JobStatus, string> = {
  queued: "text-zinc-500",
  running: "text-blue-600 dark:text-blue-400",
  done: "text-green-600",
  failed: "text-red-600",
};

/** One line for a background job: its status, what it did so far, and its error. */
export function JobProgress({ status, label, detail, error }: { status: JobStatus; label: string; detail?: string; error?: string }) {
  return (
    <p className="text-xs" role="status" data-job-status={status}>
      <span className={`font-medium ${COLOURS[status]}`}>{status}</span> {label}
      {detail ? <span className="text-zinc-500"> · {detail}</span> : null}
      {error ? <span className="block text-red-600">{error}</span> : null}
    </p>
  );
}

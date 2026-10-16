import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { api, curation, isRunning, type JobOrPlan, type PackJob, type PackPlan } from "../api";
import { JobProgress } from "../components/JobProgress";
import { planSummary } from "../jobs";
import { useUser } from "../components/Shell";

export function PacksPage() {
  const user = useUser();
  const packs = useQuery({ queryKey: ["packs"], queryFn: api.packs, enabled: user.isSuccess });
  // only editors may list pack jobs: the import panel shows when they can
  const jobs = useQuery({
    queryKey: ["pack-jobs"],
    queryFn: curation.packJobs,
    enabled: user.isSuccess,
    retry: false,
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => isRunning(j.status)) ? 2000 : false),
  });
  if (!user.isSuccess) return null;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Knowledge models</h1>
      <p className="text-sm text-zinc-500">
        Packs are ontology-skill bundles. The active version of each pack tags and classifies documents.
      </p>
      <ul className="grid gap-3 md:grid-cols-2">
        {packs.data?.map((p) => (
          <li key={p.id} className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex items-baseline justify-between">
              <Link to="/packs/$packId" params={{ packId: p.pack_id }} className="font-medium text-blue-700 hover:underline dark:text-blue-400">
                {p.title}
              </Link>
              <span className={`text-xs ${p.status === "active" ? "text-green-600" : "text-zinc-500"}`}>{p.status}</span>
            </div>
            <p className="text-xs text-zinc-500">
              {p.pack_id}@{p.version} · bundle {p.bundle_status}
            </p>
            <p className="mt-2 text-sm">
              {p.counts.concepts ?? 0} concepts · {p.counts.individuals ?? 0} individuals · {p.counts.classes ?? 0} classes ·{" "}
              {p.counts.triples ?? 0} triples
            </p>
            {p.cq_results?.tests ? (
              <p className="text-xs text-zinc-500">
                competency tests {p.cq_results.passed}/{p.cq_results.tests}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
      {jobs.isSuccess && <PackImport jobs={jobs.data} />}
    </div>
  );
}

function jobLine(j: PackJob): string {
  const what = `${j.kind} ${j.pack_id}${j.version ? `@${j.version}` : ""}`;
  const by = j.created_by ? ` by ${j.created_by}` : "";
  return `${what}${by}`;
}

function jobDetail(j: PackJob): string | undefined {
  if (j.status !== "done") return undefined;
  const r = j.result;
  const parts = [r.status, r.counts?.terms != null ? `${r.counts.terms} terms` : "", r.reindexed?.length ? `reindex ${r.reindexed.join(", ")}` : ""];
  return parts.filter(Boolean).join(" · ");
}

function PackImport({ jobs }: { jobs: PackJob[] }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"bundle" | "list">("bundle");
  const [path, setPath] = useState("");
  const [reindex, setReindex] = useState(true);
  const [force, setForce] = useState(false);
  const [list, setList] = useState({ pack_id: "", title: "", scheme: "", text: "" });
  const [plan, setPlan] = useState<PackPlan | null>(null);
  const send = useMutation({
    mutationFn: (confirm: boolean): Promise<JobOrPlan> =>
      tab === "bundle"
        ? curation.importPack({ path, activate: true, reindex, force, confirm })
        : curation.importList({ ...list, confirm }),
    onSuccess: (out) => {
      setPlan(out.plan ?? null);
      if (out.job) qc.invalidateQueries({ queryKey: ["pack-jobs"] });
    },
  });
  const running = jobs.some((j) => isRunning(j.status));
  useEffect(() => {
    if (!running) qc.invalidateQueries({ queryKey: ["packs"] });
  }, [running, qc]);
  function submit(e: FormEvent) {
    e.preventDefault();
    send.mutate(false);
  }
  const ready = tab === "bundle" ? path.trim() !== "" : list.pack_id.trim() !== "" && list.text.trim() !== "";
  const input = "w-full rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700";
  return (
    <section className="grid gap-4 md:grid-cols-2">
      <form onSubmit={submit} className="space-y-2 rounded-lg border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900" aria-label="Import a pack">
        <div className="flex items-center gap-2">
          <h2 className="mr-auto text-xs font-semibold uppercase text-zinc-500">Import</h2>
          {(["bundle", "list"] as const).map((t) => (
            <button type="button" key={t} onClick={() => { setTab(t); setPlan(null); }} className={`rounded border px-2 py-0.5 text-xs ${tab === t ? "bg-blue-100 dark:bg-blue-900/40" : ""}`}>
              {t === "bundle" ? "Bundle" : "Plain list"}
            </button>
          ))}
        </div>
        {tab === "bundle" ? (
          <>
            <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/import/my-pack (as the server sees it)" className={input} aria-label="Bundle path" />
            <label className="mr-4"><input type="checkbox" checked={reindex} onChange={(e) => setReindex(e.target.checked)} /> reindex the collections that use it</label>
            <label><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> accept a manifest mismatch</label>
          </>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-2">
              <input value={list.pack_id} onChange={(e) => setList({ ...list, pack_id: e.target.value })} placeholder="pack id" className={input} aria-label="Pack id" />
              <input value={list.title} onChange={(e) => setList({ ...list, title: e.target.value })} placeholder="title" className={input} aria-label="Title" />
              <input value={list.scheme} onChange={(e) => setList({ ...list, scheme: e.target.value })} placeholder="facet name" className={input} aria-label="Facet name" />
            </div>
            <textarea value={list.text} onChange={(e) => setList({ ...list, text: e.target.value })} rows={6} placeholder={"One name per line; other names after |\nApple | Malus domestica\nPear"} className={`${input} font-mono`} aria-label="Names" />
          </>
        )}
        <button disabled={send.isPending || !ready} className="rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700 disabled:opacity-50">
          {send.isPending ? "Checking…" : "Import"}
        </button>
        {plan && (
          <div className="rounded border border-amber-300 bg-amber-50 p-2 dark:border-amber-800 dark:bg-amber-950/40">
            <p>This replaces the active version: {planSummary(plan)}</p>
            <button type="button" onClick={() => send.mutate(true)} disabled={send.isPending} className="mt-1 rounded bg-amber-600 px-3 py-1 text-white hover:bg-amber-700 disabled:opacity-50">
              Confirm and import
            </button>
          </div>
        )}
        {send.isError && <p className="text-xs text-red-600">{String(send.error)}</p>}
        {send.data?.job && <p className="text-xs text-green-600">Queued as job {send.data.job.id}.</p>}
      </form>
      <div className="space-y-1 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
        <h2 className="text-xs font-semibold uppercase text-zinc-500">Pack jobs</h2>
        {jobs.length === 0 && <p className="text-sm text-zinc-500">No imports or activations yet.</p>}
        {jobs.map((j) => (
          <JobProgress key={j.id} status={j.status} label={jobLine(j)} detail={jobDetail(j)} error={j.error} />
        ))}
      </div>
    </section>
  );
}

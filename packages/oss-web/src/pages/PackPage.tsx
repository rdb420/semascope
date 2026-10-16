import { Link, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, curation, isRunning, type PackPlan, type TreeNode } from "../api";
import { JobProgress } from "../components/JobProgress";
import { useUser } from "../components/Shell";
import { planSummary } from "../jobs";

export function PackPage() {
  const { packId } = useParams({ from: "/packs/$packId" });
  const user = useUser();
  const pack = useQuery({ queryKey: ["pack", packId], queryFn: () => api.pack(packId), enabled: user.isSuccess });
  const [scheme, setScheme] = useState<string | undefined>(undefined);
  const tree = useQuery({
    queryKey: ["pack-tree", packId, scheme],
    queryFn: () => api.packTree(packId, scheme),
    enabled: user.isSuccess && !!pack.data,
  });
  const deltas = useQuery({ queryKey: ["deltas", packId], queryFn: () => api.deltas(packId), enabled: user.isSuccess });
  if (!user.isSuccess) return null;
  if (!pack.data) return <p className="text-zinc-500">Loading…</p>;
  const p = pack.data;
  const pending = (deltas.data ?? []).filter((d) => d.status === "draft");
  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_20rem]">
      <div className="space-y-4">
        <div>
          <Link to="/packs" className="text-sm text-blue-700 hover:underline dark:text-blue-400">← Knowledge models</Link>
          <h1 className="text-xl font-semibold">{p.title}</h1>
          <p className="text-xs text-zinc-500">{p.pack_id}@{p.version} · {p.status} · {p.base_iri}</p>
        </div>
        <div className="flex flex-wrap gap-2 text-sm">
          <button onClick={() => setScheme(undefined)} className={`rounded border px-2 py-1 ${scheme === undefined ? "bg-blue-100 dark:bg-blue-900/40" : ""}`}>All</button>
          {p.schemes.map((s) => (
            <button key={s.slug} onClick={() => setScheme(s.slug)} className={`rounded border px-2 py-1 ${scheme === s.slug ? "bg-blue-100 dark:bg-blue-900/40" : ""}`} title={s.definition}>
              {s.label}
            </button>
          ))}
          <button onClick={() => setScheme("classes")} className={`rounded border px-2 py-1 ${scheme === "classes" ? "bg-blue-100 dark:bg-blue-900/40" : ""}`}>Classes</button>
        </div>
        <ul className="rounded-lg border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900">
          {tree.data?.map((n) => <Node key={n.iri} node={n} depth={0} />)}
          {tree.data?.length === 0 && <li className="text-zinc-500">Nothing in this scheme.</li>}
        </ul>
      </div>
      <aside className="space-y-4 text-sm">
        <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Scope</h2>
          <ul className="list-disc space-y-1 pl-4">{(p.requirements.scope?.inclusions ?? []).map((x: string) => <li key={x}>{x}</li>)}</ul>
          {p.requirements.scope?.exclusions?.length ? (
            <>
              <h3 className="mt-3 text-xs font-semibold uppercase text-zinc-500">Not modelled</h3>
              <ul className="list-disc space-y-1 pl-4 text-zinc-500">{p.requirements.scope.exclusions.map((x: string) => <li key={x}>{x}</li>)}</ul>
            </>
          ) : null}
        </section>
        <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Competency questions</h2>
          <ul className="space-y-2">
            {(p.requirements.competency_questions ?? []).map((cq: { id: string; question: string; priority: string }) => {
              const result = p.cq_results?.results?.find((r: { cq_id: string }) => r.cq_id === cq.id);
              return (
                <li key={cq.id}>
                  <span className={result?.status === "pass" ? "text-green-600" : result ? "text-red-600" : "text-zinc-500"}>{result?.status ?? "no test"}</span>{" "}
                  {cq.question} <span className="text-zinc-500">({cq.priority})</span>
                </li>
              );
            })}
          </ul>
        </section>
        <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Proposed changes ({pending.length})</h2>
          <ul className="space-y-1">
            {pending.map((d) => (
              <li key={d.id}>
                <span className="font-medium">{d.kind.replace(/_/g, " ")}</span> {String(d.params.label ?? d.params.text ?? "")}{" "}
                <span className="text-zinc-500">{d.reason}</span>
              </li>
            ))}
          </ul>
          {pending.length > 0 && (
            <a href={`/api/knowledge/packs/${p.pack_id}/deltas/export`} className="mt-2 inline-block text-blue-700 hover:underline dark:text-blue-400">
              Export as a bundle delta
            </a>
          )}
        </section>
        <Versions packId={p.pack_id} />
        <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Sources</h2>
          <ul className="space-y-1">{p.sources.map((s: { id: string; title: string; location?: string }) => <li key={s.id}>{s.location ? <a href={s.location} className="hover:underline" target="_blank" rel="noreferrer">{s.title}</a> : s.title}</li>)}</ul>
        </section>
        <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Decisions</h2>
          <ul className="space-y-2">{p.decisions.map((d: { id: string; topic: string; decision: string; rationale: string }) => <li key={d.id}><span className="font-medium">{d.topic.replace(/_/g, " ")}:</span> {d.decision} <span className="text-zinc-500">{d.rationale}</span></li>)}</ul>
        </section>
      </aside>
    </div>
  );
}

function Node({ node, depth }: { node: TreeNode; depth: number }) {
  return (
    <li>
      <Link
        to="/concepts"
        search={{ iri: node.iri }}
        style={{ paddingLeft: `${depth * 1}rem` }}
        className="block rounded py-0.5 hover:bg-zinc-100 dark:hover:bg-zinc-800"
      >
        {node.label} <span className="text-xs text-zinc-400">{node.kind}</span>
      </Link>
      {node.children.length > 0 && <ul>{node.children.map((c) => <Node key={c.iri} node={c} depth={depth + 1} />)}</ul>}
    </li>
  );
}

/** The pack's imported versions; editors activate one (upgrade or roll back) after its plan. */
function Versions({ packId }: { packId: string }) {
  const qc = useQueryClient();
  const versions = useQuery({ queryKey: ["pack-versions", packId], queryFn: () => curation.packVersions(packId) });
  const jobs = useQuery({
    queryKey: ["pack-jobs"],
    queryFn: curation.packJobs,
    retry: false,
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => isRunning(j.status)) ? 2000 : false),
  });
  const [plan, setPlan] = useState<{ version: string; plan: PackPlan } | null>(null);
  const activate = useMutation({
    mutationFn: (a: { version: string; confirm: boolean }) => curation.activatePack(packId, a.version, a.confirm),
    onSuccess: (out, a) => {
      setPlan(out.plan ? { version: a.version, plan: out.plan } : null);
      if (out.job) {
        qc.invalidateQueries({ queryKey: ["pack-jobs"] });
        qc.invalidateQueries({ queryKey: ["pack-versions", packId] });
      }
    },
  });
  const latest = (jobs.data ?? []).find((j) => j.pack_id === packId);
  if (!versions.data || versions.data.length < 2 || !jobs.isSuccess) return null;
  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Versions</h2>
      <ul className="space-y-1">
        {versions.data.map((v) => (
          <li key={v.id} className="flex items-center gap-2">
            <span className="font-medium">{v.version}</span>
            <span className={`text-xs ${v.status === "active" ? "text-green-600" : "text-zinc-500"}`}>{v.status}</span>
            {v.status !== "active" && (
              <button onClick={() => activate.mutate({ version: v.version, confirm: false })} disabled={activate.isPending} className="ml-auto rounded border px-2 py-0.5 text-xs disabled:opacity-50">
                Activate
              </button>
            )}
          </li>
        ))}
      </ul>
      {plan && (
        <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-2 dark:border-amber-800 dark:bg-amber-950/40">
          <p>{planSummary(plan.plan)}</p>
          <button onClick={() => activate.mutate({ version: plan.version, confirm: true })} className="mt-1 rounded bg-amber-600 px-3 py-1 text-white hover:bg-amber-700">
            Confirm activation
          </button>
        </div>
      )}
      {activate.isError && <p className="text-xs text-red-600">{String(activate.error)}</p>}
      {latest && <JobProgress status={latest.status} label={`${latest.kind} ${latest.version}`} error={latest.error} />}
    </section>
  );
}

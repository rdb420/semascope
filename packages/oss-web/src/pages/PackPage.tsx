import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type TreeNode } from "../api";
import { useUser } from "../components/Shell";

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

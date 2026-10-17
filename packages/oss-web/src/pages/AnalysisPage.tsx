import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { analysis, type CompareTerm, type TreeNode } from "../extrasApi";
import type { AnalysisParams, AnalysisTab } from "../extrasParams";
import { useUser } from "../components/Shell";
import { SearchSummary, ViewLinks } from "../components/ViewLinks";
import { toSearchIn } from "../viewParams";

const PANEL = "rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900";
const INPUT = "rounded border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700";
const TABS: { value: AnalysisTab; label: string; title: string }[] = [
  { value: "clusters", label: "Clusters", title: "groups of documents that use the same words" },
  { value: "compare", label: "Compare", title: "words that set these results apart from others" },
  { value: "tree", label: "Word tree", title: "what follows or precedes a word in these results" },
];

/** Analysis views of a search's results: clusters, corpus comparison and a word tree. */
export function AnalysisPage() {
  const params = useSearch({ from: "/analysis" }) as AnalysisParams;
  const navigate = useNavigate({ from: "/analysis" });
  const user = useUser();
  const tab = params.tab ?? "clusters";
  if (!user.isSuccess) return null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <h1 className="mr-2 text-xl font-semibold">Analysis</h1>
        <ViewLinks params={params} current="analysis" />
      </div>
      <SearchSummary params={params} />
      <div className="flex gap-1" role="tablist" aria-label="Analysis">
        {TABS.map((t) => (
          <button
            key={t.value}
            role="tab"
            aria-selected={tab === t.value}
            title={t.title}
            onClick={() => navigate({ search: (s: AnalysisParams) => ({ ...s, tab: t.value === "clusters" ? undefined : t.value }) })}
            className={`rounded border px-3 py-1 text-sm ${tab === t.value ? "bg-blue-100 dark:bg-blue-900/40" : ""}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "clusters" && <Clusters params={params} />}
      {tab === "compare" && <Compare params={params} />}
      {tab === "tree" && <WordTree params={params} />}
    </div>
  );
}

function Clusters({ params }: { params: AnalysisParams }) {
  const navigate = useNavigate({ from: "/analysis" });
  const [features, setFeatures] = useState<"terms" | "vectors">("terms");
  const result = useQuery({
    queryKey: ["clusters", params, features],
    queryFn: () => analysis.clusters(toSearchIn(params), params.k ?? null, features),
  });
  const data = result.data;
  return (
    <section aria-label="Clusters" className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label>
          clusters{" "}
          <select
            aria-label="Number of clusters"
            value={params.k ?? ""}
            onChange={(e) => navigate({ search: (s: AnalysisParams) => ({ ...s, k: e.target.value ? Number(e.target.value) : undefined }) })}
            className={INPUT}
          >
            <option value="">automatic</option>
            {[2, 3, 4, 5, 6, 8, 10, 12].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <label>
          by{" "}
          <select aria-label="Group by" value={features} onChange={(e) => setFeatures(e.target.value as "terms" | "vectors")} className={INPUT}>
            <option value="terms">words</option>
            <option value="vectors">meaning</option>
          </select>
        </label>
        {data && (
          <span className="text-zinc-500">
            {data.documents} documents in {data.k} clusters
          </span>
        )}
      </div>
      {result.isLoading && <p className="text-sm text-zinc-500">Grouping…</p>}
      {result.isError && <p className="text-red-600">{String(result.error)}</p>}
      {data?.vector_error && <p className="text-xs text-zinc-500">Meaning is unavailable here; grouped by words.</p>}
      {data && data.documents === 0 && <p className="text-sm text-zinc-500">No documents to group.</p>}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {data?.clusters.map((c) => (
          <article key={c.id} className={PANEL} aria-label={`Cluster ${c.id}`}>
            <h2 className="mb-1 flex items-baseline justify-between text-sm font-semibold">
              <span>{c.terms.slice(0, 3).map((t) => t.term).join(", ") || `Cluster ${c.id}`}</span>
              <span className="font-normal text-zinc-500" title="documents; how alike they are (0–1)">
                {c.size} · {c.coherence.toFixed(2)}
              </span>
            </h2>
            <p className="mb-2 flex flex-wrap gap-1 text-xs">
              {c.terms.map((t) => (
                <Link key={t.term} to="/" search={{ q: t.term, collection: params.collection }} className="rounded-full border border-zinc-300 px-2 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800">
                  {t.term}
                </Link>
              ))}
            </p>
            <ul className="space-y-0.5 text-sm">
              {c.documents.map((d) => (
                <li key={d.id} className="flex gap-2">
                  <Link to="/documents/$id" params={{ id: d.id }} className="truncate text-blue-700 hover:underline dark:text-blue-400">
                    {d.title}
                  </Link>
                  <span className="ml-auto shrink-0 text-xs tabular-nums text-zinc-400">{d.similarity.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </section>
  );
}

function Terms({ title, rows, side }: { title: string; rows: CompareTerm[]; side: "a" | "b" }) {
  const most = Math.max(1e-9, ...rows.map((r) => Math.abs(r.g2)));
  return (
    <section className={PANEL} aria-label={title}>
      <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">{title}</h2>
      {rows.length === 0 && <p className="text-sm text-zinc-500">None stands out.</p>}
      <ul className="space-y-0.5 text-sm">
        {rows.map((r) => (
          <li key={r.term} className="relative flex justify-between rounded px-1" title={`in ${r.a_docs} of these documents and ${r.b_docs} of the others; G² ${r.g2}`}>
            <span aria-hidden className="absolute inset-y-0 left-0 rounded bg-blue-100/70 dark:bg-blue-900/40" style={{ width: `${(Math.abs(r.g2) / most) * 100}%` }} />
            <span className="relative">{r.term}</span>
            <span className="relative tabular-nums text-xs text-zinc-500">
              {Math.round((side === "a" ? r.a_share : r.b_share) * 100)}% vs {Math.round((side === "a" ? r.b_share : r.a_share) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Compare({ params }: { params: AnalysisParams }) {
  const navigate = useNavigate({ from: "/analysis" });
  const [draft, setDraft] = useState(params.vs ?? "");
  const against = params.vs ? toSearchIn({ q: params.vs, collection: params.collection }) : null;
  const result = useQuery({
    queryKey: ["compare", params],
    queryFn: () => analysis.compare(toSearchIn(params), against),
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    navigate({ search: (s: AnalysisParams) => ({ ...s, vs: draft.trim() || undefined }) });
  }
  const data = result.data;
  return (
    <div className="space-y-3">
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2 text-sm" aria-label="Compare with">
        <label htmlFor="vs">against</label>
        <input id="vs" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="another search (empty: the whole collection)" className={`${INPUT} min-w-72 flex-1`} />
        <button className="rounded bg-blue-600 px-3 py-1 text-white">Compare</button>
      </form>
      {result.isLoading && <p className="text-sm text-zinc-500">Counting…</p>}
      {result.isError && <p className="text-red-600">{String(result.error)}</p>}
      {data && (
        <>
          <p className="text-sm text-zinc-500">
            {data.a_documents} documents of this search against {data.b_documents} {data.background === "index" ? "documents of the collections searched" : `documents for “${params.vs}”`}
          </p>
          <div className="grid gap-4 md:grid-cols-2">
            <Terms title="More typical of this search" rows={data.more} side="a" />
            <Terms title={data.background === "index" ? "Less typical of this search" : `More typical of “${params.vs}”`} rows={data.less} side="b" />
          </div>
        </>
      )}
    </div>
  );
}

function Branch({ node }: { node: TreeNode }) {
  const size = Math.max(0.75, Math.min(1.6, 0.75 + Math.log2(node.count + 1) / 6));
  return (
    <li>
      <span style={{ fontSize: `${size}rem` }} title={`${node.count} times in ${node.documents} documents`}>
        {node.word}
      </span>{" "}
      <span className="text-xs text-zinc-400">{node.count}</span>
      {node.children.length > 0 && (
        <ul className="ml-4 border-l border-zinc-200 pl-3 dark:border-zinc-700">
          {node.children.map((c) => (
            <Branch key={c.word + c.count} node={c} />
          ))}
          {node.more > 0 && <li className="text-xs text-zinc-400">… {node.more} more</li>}
        </ul>
      )}
    </li>
  );
}

function WordTree({ params }: { params: AnalysisParams }) {
  const navigate = useNavigate({ from: "/analysis" });
  const initial = params.word ?? (params.q && params.q !== "*:*" ? params.q.split(/\s+/)[0].replace(/[^\p{L}\p{N}'-]/gu, "") : "");
  const [draft, setDraft] = useState(initial);
  const word = params.word ?? initial;
  const side = params.side ?? "right";
  const result = useQuery({
    queryKey: ["wordtree", params, word],
    queryFn: () => analysis.wordTree(toSearchIn(params), word, side),
    enabled: !!word,
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    navigate({ search: (s: AnalysisParams) => ({ ...s, word: draft.trim() || undefined }) });
  }
  const data = result.data;
  return (
    <div className="space-y-3">
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2 text-sm" aria-label="Word tree">
        <label htmlFor="word">word or phrase</label>
        <input id="word" value={draft} onChange={(e) => setDraft(e.target.value)} className={INPUT} />
        <select aria-label="Direction" value={side} onChange={(e) => navigate({ search: (s: AnalysisParams) => ({ ...s, side: e.target.value === "left" ? "left" : undefined }) })} className={INPUT}>
          <option value="right">what follows</option>
          <option value="left">what precedes</option>
        </select>
        <button className="rounded bg-blue-600 px-3 py-1 text-white">Grow</button>
      </form>
      {result.isLoading && <p className="text-sm text-zinc-500">Reading…</p>}
      {result.isError && <p className="text-red-600">{String(result.error)}</p>}
      {data && (
        <section className={PANEL} aria-label="Tree">
          <p className="mb-2 text-sm text-zinc-500">
            {data.occurrences} occurrences in {data.documents} of {data.sampled} documents{data.truncated ? " (stopped at the cap)" : ""}
          </p>
          {data.occurrences === 0 ? (
            <p className="text-sm text-zinc-500">The phrase does not occur in these documents.</p>
          ) : (
            <ul className="font-serif">
              <Branch node={data.root} />
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

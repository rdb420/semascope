import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api, edgeKey, views, type GraphEdge, type GraphOut, type SearchIn } from "../api";
import { GraphView } from "../components/GraphView";
import { useUser } from "../components/Shell";
import { SearchSummary, ViewLinks } from "../components/ViewLinks";
import { toSearchIn } from "../viewParams";

export function GraphPage() {
  const { id, collection } = useSearch({ from: "/graph" });
  const user = useUser();
  const navigate = useNavigate({ from: "/graph" });
  const [depth, setDepth] = useState(1);
  const collectionsQ = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const [other, setOther] = useState("");
  const [pathTarget, setPathTarget] = useState<string | null>(null);
  const hubsAll = useQuery({ queryKey: ["graph-hubs-all", collection], queryFn: () => api.graphShared(collection || undefined), enabled: user.isSuccess && !!id });
  const pathQ = useQuery({
    queryKey: ["graph-path", id, pathTarget],
    queryFn: () => api.graphPath(id, pathTarget!),
    enabled: user.isSuccess && !!id && !!pathTarget,
  });
  const hubs = useQuery({
    queryKey: ["graph-hubs", collection],
    queryFn: () => api.graphShared(collection || undefined),
    enabled: user.isSuccess && !id,
  });
  const graph = useQuery({
    queryKey: ["graph-neighbours", id, depth, collection],
    queryFn: () => api.graphNeighbours(id, depth, collection || undefined),
    enabled: user.isSuccess && !!id,
  });
  const [edge, setEdge] = useState<GraphEdge | null>(null);
  const select = useCallback(
    (nodeId: string, kind: string) => {
      setEdge(null);
      if (kind === "Document") navigate({ to: "/documents/$id", params: { id: nodeId.slice("Document:".length) } });
      else navigate({ search: (prev) => ({ ...prev, id: nodeId }) });
    },
    [navigate],
  );
  if (!user.isSuccess) return null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Graph</h1>
        <select
          value={collection}
          onChange={(e) => navigate({ search: (prev) => ({ ...prev, collection: e.target.value }) })}
          className="rounded border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700"
        >
          <option value="">All collections</option>
          {collectionsQ.data?.map((c) => (
            <option key={c.slug} value={c.slug}>{c.name}</option>
          ))}
        </select>
        {id && (
          <>
            <span className="truncate text-sm text-zinc-500">{id}</span>
            <label className="text-sm">
              depth{" "}
              <select value={depth} onChange={(e) => setDepth(Number(e.target.value))} className="rounded border border-zinc-300 bg-transparent px-1 dark:border-zinc-700">
                <option value={1}>1</option>
                <option value={2}>2</option>
                <option value={3}>3</option>
              </select>
            </label>
            <button onClick={() => navigate({ search: { id: "", collection } })} className="text-sm text-blue-700 hover:underline dark:text-blue-400">
              Back to hubs
            </button>
            <span className="ml-auto flex items-center gap-1 text-sm">
              <span className="text-zinc-500">path to</span>
              <select value={other} onChange={(e) => setOther(e.target.value)} className="rounded border border-zinc-300 bg-transparent px-1 dark:border-zinc-700">
                <option value="">choose…</option>
                {hubsAll.data?.map((h) => {
                  const hid = `${h.labels.includes("Concept") ? "Concept" : "Entity"}:${h.key}`;
                  return hid === id ? null : (
                    <option key={hid} value={hid}>{h.label}</option>
                  );
                })}
              </select>
              <button disabled={!other} onClick={() => setPathTarget(other)} className="rounded border px-2 py-0.5 disabled:opacity-40">
                Find
              </button>
              {pathTarget && (
                <button onClick={() => setPathTarget(null)} className="text-zinc-500 hover:underline">
                  clear
                </button>
              )}
            </span>
          </>
        )}
      </div>
      {!id && (
        <section>
          <p className="mb-2 text-sm text-zinc-500">The entities and concepts that connect the most documents. Pick one to see its neighbourhood.</p>
          <ul className="grid gap-2 md:grid-cols-3">
            {hubs.data?.map((h) => (
              <li key={h.key}>
                <button
                  onClick={() => navigate({ search: { id: `${h.labels.includes("Concept") ? "Concept" : "Entity"}:${h.key}`, collection } })}
                  className="flex w-full items-center justify-between rounded border border-zinc-200 bg-white px-3 py-2 text-left text-sm hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:bg-zinc-800"
                >
                  <span>{h.label}</span>
                  <span className="text-zinc-500">{h.docs} docs</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {id && pathTarget && pathQ.data && (
        <>
          <p className="text-sm text-zinc-500">
            {pathQ.data.nodes.length ? `shortest path: ${pathQ.data.nodes.map((n) => n.label).join(" → ")}` : "no path within 4 steps"}
          </p>
          {pathQ.data.nodes.length > 0 && <GraphView graph={pathQ.data} onSelect={select} height={260} />}
        </>
      )}
      {id && graph.data && (
        <>
          <p className="text-sm text-zinc-500">
            {graph.data.nodes.length} nodes · {graph.data.edges.length} edges · click a node to expand it, a document to open it, an edge to see the documents behind it
          </p>
          <GraphView graph={graph.data} onSelect={select} onEdge={setEdge} />
          <EdgeList graph={graph.data} selected={edge} onPick={setEdge} />
          {edge && <EdgePanel edge={edge} graph={graph.data} collection={collection || null} onClose={() => setEdge(null)} />}
        </>
      )}
      {graph.isError && <p className="text-red-600">{String(graph.error)}</p>}
    </div>
  );
}

function labelsOf(graph: GraphOut): Map<string, string> {
  return new Map(graph.nodes.map((n) => [n.id, n.label]));
}

/** The edges as a list (heaviest first), for keyboards and for edges too thin to click. */
function EdgeList({ graph, selected, onPick }: { graph: GraphOut; selected: GraphEdge | null; onPick: (e: GraphEdge) => void }) {
  const labels = useMemo(() => labelsOf(graph), [graph]);
  const edges = useMemo(
    () => [...graph.edges].sort((a, b) => Number(b.properties.docs ?? 0) - Number(a.properties.docs ?? 0)).slice(0, 100),
    [graph],
  );
  if (!edges.length) return null;
  return (
    <details className="rounded-lg border border-zinc-200 bg-white p-3 text-sm dark:border-zinc-800 dark:bg-zinc-900">
      <summary className="cursor-pointer text-zinc-500">Edges ({graph.edges.length})</summary>
      <ul className="mt-2 grid gap-1 md:grid-cols-2">
        {edges.map((e) => (
          <li key={edgeKey(e)}>
            <button
              onClick={() => onPick(e)}
              aria-pressed={selected ? edgeKey(selected) === edgeKey(e) : false}
              className={`w-full truncate rounded px-1 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800 ${selected && edgeKey(selected) === edgeKey(e) ? "bg-blue-100 dark:bg-blue-900/40" : ""}`}
            >
              {labels.get(e.source) ?? e.source} — {labels.get(e.target) ?? e.target}
              <span className="ml-1 text-zinc-500">{e.type === "CO_OCCURS" ? `${String(e.properties.docs)} docs` : e.type.toLowerCase()}</span>
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** The documents behind an edge: where both ends occur, or that share a document's neighbour. */
function EdgePanel({
  edge,
  graph,
  collection,
  search,
  onClose,
}: {
  edge: GraphEdge;
  graph: GraphOut;
  collection: string | null;
  search?: SearchIn | null;
  onClose: () => void;
}) {
  const labels = labelsOf(graph);
  const docs = useQuery({
    queryKey: ["edge-documents", edge.source, edge.target, collection, search],
    queryFn: () => views.edgeDocuments({ a: edge.source, b: edge.target, collection, search }),
  });
  const docEnd = edge.source.startsWith("Document:") || edge.target.startsWith("Document:");
  const other = edge.source.startsWith("Document:") ? edge.target : edge.source;
  const q = search?.q && search.q !== "*:*" ? search.q : undefined;
  return (
    <section aria-label="Edge documents" className="rounded-lg border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-2 flex items-center gap-2 font-medium">
        {docEnd ? (
          <>Documents with {labels.get(other) ?? other}</>
        ) : (
          <>
            Documents with both {labels.get(edge.source) ?? edge.source} and {labels.get(edge.target) ?? edge.target}
          </>
        )}
        <button onClick={onClose} className="ml-auto text-xs text-zinc-500 hover:underline">
          close
        </button>
      </h2>
      {docs.isError && <p className="text-red-600">{String(docs.error)}</p>}
      {docs.data && (
        <ul className="space-y-0.5">
          {docs.data.documents.map((d) => (
            <li key={d.id} className="flex gap-2">
              <Link to="/documents/$id" params={{ id: d.id }} search={q ? { q } : {}} className="truncate text-blue-700 hover:underline dark:text-blue-400">
                {d.title}
              </Link>
              <span className="text-zinc-400">{d.collection}</span>
            </li>
          ))}
          {docs.data.documents.length === 0 && <li className="text-zinc-500">No readable documents.</li>}
          {docs.data.more && <li className="text-zinc-500">… and more</li>}
        </ul>
      )}
    </section>
  );
}

/** The co-occurrence graph of a search: concepts and entities that occur in the same results. */
export function SearchGraphPage() {
  const params = useSearch({ from: "/graph/results" });
  const user = useUser();
  const [focus, setFocus] = useState<string | null>(null);
  const [size, setSize] = useState(40);
  const [edge, setEdge] = useState<GraphEdge | null>(null);
  const search = toSearchIn(params);
  const graph = useQuery({
    queryKey: ["cooccurrence", params, focus, size],
    queryFn: () => views.cooccurrence({ search, collection: params.collection ?? null, node: focus, nodes: size, edges: size * 4 }),
    enabled: user.isSuccess,
  });
  const select = useCallback((nodeId: string, kind: string) => {
    if (kind === "Concept" || kind === "Entity") {
      setEdge(null);
      setFocus(nodeId);
    }
  }, []);
  if (!user.isSuccess) return null;
  const focusLabel = focus ? graph.data?.nodes.find((n) => n.id === focus)?.label ?? focus : null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <h1 className="mr-2 text-xl font-semibold">Graph of the results</h1>
        <ViewLinks params={params} current="graph" />
        <label className="ml-auto text-zinc-500">
          nodes{" "}
          <select value={size} onChange={(e) => setSize(Number(e.target.value))} className="rounded border border-zinc-300 bg-transparent px-1 dark:border-zinc-700">
            {[20, 40, 80].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>
      <SearchSummary params={params} />
      <p className="text-sm text-zinc-500">
        {focus ? (
          <>
            What occurs with <strong>{focusLabel}</strong> in these results.{" "}
            <button onClick={() => setFocus(null)} className="text-blue-700 hover:underline dark:text-blue-400">
              whole network
            </button>{" "}
            ·{" "}
            <Link to="/graph" search={{ id: focus, collection: params.collection ?? "" }} className="text-blue-700 hover:underline dark:text-blue-400">
              neighbourhood
            </Link>
          </>
        ) : (
          <>Concepts and entities that occur in the same documents of these results (the top 500 by keyword relevance); a thicker edge means more shared documents. Click a node to centre on it, an edge to see its documents.</>
        )}
      </p>
      {graph.isError && <p className="text-red-600">{String(graph.error)}</p>}
      {graph.data && graph.data.nodes.length === 0 && <p className="text-sm text-zinc-500">Nothing occurs together in these results.</p>}
      {graph.data && graph.data.nodes.length > 0 && (
        <>
          <p className="text-sm text-zinc-500">
            {graph.data.nodes.length} nodes · {graph.data.edges.length} edges
          </p>
          <GraphView graph={graph.data} onSelect={select} onEdge={setEdge} />
          <EdgeList graph={graph.data} selected={edge} onPick={setEdge} />
          {edge && <EdgePanel edge={edge} graph={graph.data} collection={params.collection ?? null} search={search} onClose={() => setEdge(null)} />}
        </>
      )}
    </div>
  );
}

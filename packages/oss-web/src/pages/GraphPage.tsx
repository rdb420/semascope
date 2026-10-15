import { useCallback, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { GraphView } from "../components/GraphView";
import { useUser } from "../components/Shell";

export function GraphPage() {
  const { id, collection } = useSearch({ from: "/graph" });
  const user = useUser();
  const navigate = useNavigate({ from: "/graph" });
  const [depth, setDepth] = useState(1);
  const hubs = useQuery({
    queryKey: ["graph-hubs", collection],
    queryFn: () => api.graphShared(collection || undefined),
    enabled: user.isSuccess && !id,
  });
  const graph = useQuery({
    queryKey: ["graph-neighbours", id, depth],
    queryFn: () => api.graphNeighbours(id, depth),
    enabled: user.isSuccess && !!id,
  });
  const select = useCallback(
    (nodeId: string, kind: string) => {
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
      {id && graph.data && (
        <>
          <p className="text-sm text-zinc-500">
            {graph.data.nodes.length} nodes · {graph.data.edges.length} edges · click a node to expand it, a document to open it
          </p>
          <GraphView graph={graph.data} onSelect={select} />
        </>
      )}
      {graph.isError && <p className="text-red-600">{String(graph.error)}</p>}
    </div>
  );
}

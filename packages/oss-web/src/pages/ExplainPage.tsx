import { Link, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { analysis, type ExplainNode } from "../extrasApi";
import { explainSearch, type ExplainParams } from "../extrasParams";
import { useUser } from "../components/Shell";
import { SearchSummary } from "../components/ViewLinks";

const PANEL = "rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900";

function Tree({ node }: { node: ExplainNode }) {
  return (
    <li>
      <span className="tabular-nums text-zinc-500">{node.value?.toFixed(4)}</span> {node.description}
      {node.details && node.details.length > 0 && (
        <ul className="ml-4 border-l border-zinc-200 pl-2 dark:border-zinc-700">
          {node.details.map((d, i) => (
            <Tree key={i} node={d} />
          ))}
        </ul>
      )}
    </li>
  );
}

const rank = (r: number | null | undefined) => (r == null ? "not in the first 1,000" : `#${r}`);

/** Why a hit ranked where it did: keyword score and clauses, nearest passages, fused shares. */
export function ExplainPage() {
  const params = useSearch({ from: "/explain" }) as ExplainParams;
  const user = useUser();
  const result = useQuery({
    queryKey: ["explain", params],
    queryFn: () => analysis.explain(explainSearch(params), params.doc),
    enabled: user.isSuccess && !!params.doc,
  });
  if (!user.isSuccess) return null;
  const e = result.data;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Why this result</h1>
      <SearchSummary params={params} />
      {!params.doc && <p className="text-sm text-zinc-500">Open this page from a search result's “why?” link.</p>}
      {result.isLoading && <p className="text-sm text-zinc-500">Asking the search engine…</p>}
      {result.isError && <p className="text-red-600">{String(result.error)}</p>}
      {e && (
        <>
          <p>
            <Link to="/documents/$id" params={{ id: e.doc_id }} className="font-medium text-blue-700 hover:underline dark:text-blue-400">
              {e.title}
            </Link>{" "}
            <span className="text-sm text-zinc-500">in {e.collection}</span>
          </p>
          {e.fused && (
            <section className={PANEL} aria-label="Fused ranking">
              <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Hybrid: both signals fused</h2>
              <p className="text-sm">
                Rank {rank(e.fused.rank)}: score {e.fused.score.toFixed(5)} = words {e.fused.keyword_share.toFixed(5)} + meaning {e.fused.semantic_share.toFixed(5)}{" "}
                <span className="text-zinc-500">(each signal adds 1 / (60 + its rank − 1))</span>
              </p>
            </section>
          )}
          {e.keyword && (
            <section className={PANEL} aria-label="Keyword ranking">
              <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Words</h2>
              <p className="mb-2 text-sm">
                {e.keyword.matched ? (
                  <>
                    Rank {rank(e.keyword.rank)} by words, score {e.keyword.score?.toFixed(4)}.
                  </>
                ) : (
                  "The words of the query do not match this document."
                )}
              </p>
              {e.keyword.clauses.length > 0 && (
                <table className="mb-2 text-sm" aria-label="Matching clauses">
                  <thead>
                    <tr className="text-left text-xs text-zinc-500">
                      <th className="pr-4 font-normal">field: word</th>
                      <th className="font-normal">weight</th>
                    </tr>
                  </thead>
                  <tbody>
                    {e.keyword.clauses.map((c) => (
                      <tr key={c.clause}>
                        <td className="pr-4 font-mono text-xs">{c.clause}</td>
                        <td className="tabular-nums">{c.weight.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {e.keyword.explanation && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-zinc-500">
                    The search engine's explanation{e.full ? "" : " (without collection-wide statistics)"}
                  </summary>
                  <ul className="mt-1 font-mono">
                    <Tree node={e.keyword.explanation} />
                  </ul>
                </details>
              )}
            </section>
          )}
          {e.semantic && (
            <section className={PANEL} aria-label="Semantic ranking">
              <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Meaning</h2>
              <p className="mb-2 text-sm">
                Rank {rank(e.semantic.rank)} by its best passage, among {e.semantic.candidates} passages nearest the query.
              </p>
              <ul className="space-y-2 text-sm">
                {e.semantic.passages.map((p, i) => (
                  <li key={i} className="border-l-2 border-blue-300 pl-2">
                    <span className="text-xs text-zinc-500">
                      similarity {p.similarity.toFixed(3)}
                      {p.page ? ` · page ${p.page}` : ""}
                      {p.chunk_rank ? ` · passage #${p.chunk_rank} of the search` : ""}
                    </span>
                    <p>{p.text}</p>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {e.semantic_error && <p className="text-sm text-zinc-500">Meaning-based ranking is unavailable here.</p>}
        </>
      )}
    </div>
  );
}

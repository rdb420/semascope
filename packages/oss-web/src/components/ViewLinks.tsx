import { Link } from "@tanstack/react-router";
import type { ViewParams } from "../viewParams";

/** The search state without view-specific keys, for links between the views. */
function shared(p: ViewParams): ViewParams {
  const out: ViewParams = {};
  if (p.q) out.q = p.q;
  if (p.collection) out.collection = p.collection;
  if (p.f && Object.keys(p.f).length) out.f = p.f;
  if (p.mode) out.mode = p.mode;
  return out;
}

const tab = "rounded border px-2 py-0.5";
const active = "bg-blue-100 dark:bg-blue-900/40";

/** Links to the other views of the same search: list, table, entities, graph, analysis, export. */
export function ViewLinks({ params, current }: { params: ViewParams; current?: "list" | "table" | "entities" | "graph" | "analysis" | "export" }) {
  const s = shared(params);
  return (
    <>
      {current && current !== "list" && (
        <Link to="/" search={s} className={tab}>
          list
        </Link>
      )}
      <Link to="/table" search={s} className={`${tab} ${current === "table" ? active : ""}`}>
        table
      </Link>
      <Link to="/entities" search={s} className={`${tab} ${current === "entities" ? active : ""}`}>
        entities
      </Link>
      <Link to="/graph/results" search={s} className={`${tab} ${current === "graph" ? active : ""}`} title="concepts and entities that occur together in these results">
        graph
      </Link>
      <Link to="/analysis" search={s} className={`${tab} ${current === "analysis" ? active : ""}`} title="clusters, comparison and a word tree of these results">
        analysis
      </Link>
      <Link to="/export" search={s} className={`${tab} ${current === "export" ? active : ""}`} title="these results as CSV, JSON Lines, Parquet or RDF">
        export
      </Link>
    </>
  );
}

/** One line naming the search a view shows. */
export function SearchSummary({ params }: { params: ViewParams }) {
  const filters = Object.entries(params.f ?? {}).flatMap(([k, vs]) => vs.map((v) => `${k}: ${v}`));
  return (
    <p className="text-sm text-zinc-500">
      {params.q && params.q !== "*:*" ? <>for <strong className="text-zinc-800 dark:text-zinc-200">{params.q}</strong></> : "all documents"}
      {params.collection ? <> in {params.collection}</> : null}
      {params.mode ? <> ({params.mode})</> : null}
      {filters.length ? <> · {filters.join(" · ")}</> : null}
    </p>
  );
}

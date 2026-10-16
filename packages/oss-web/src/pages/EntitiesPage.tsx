import { useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { views, type EntityFacet } from "../api";
import { useUser } from "../components/Shell";
import { SearchSummary, ViewLinks } from "../components/ViewLinks";
import { toSearchIn } from "../viewParams";

/** The entities view: every entity, concept and pattern facet of a search as a grid. */
export function EntitiesPage() {
  const params = useSearch({ from: "/entities" });
  const navigate = useNavigate();
  const user = useUser();
  const [limit, setLimit] = useState(20);
  const facets = useQuery({
    queryKey: ["entities", params, limit],
    queryFn: () => views.entities(toSearchIn(params), limit),
    enabled: user.isSuccess,
    placeholderData: (prev) => prev,
  });

  /** Back to the result list, filtered on this value as well. */
  function narrow(field: string, value: string) {
    const f = { ...(params.f ?? {}) };
    f[field] = [...new Set([...(f[field] ?? []), value])];
    navigate({ to: "/", search: { q: params.q, collection: params.collection, mode: params.mode, f } });
  }

  if (!user.isSuccess) return null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <h1 className="mr-2 text-xl font-semibold">Entities</h1>
        <ViewLinks params={params} current="entities" />
        <label className="ml-auto text-zinc-500">
          values per facet{" "}
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} className="rounded border border-zinc-300 bg-transparent px-1 dark:border-zinc-700">
            {[10, 20, 50, 100].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>
      <SearchSummary params={params} />
      {facets.isError && <p className="text-red-600">{String(facets.error)}</p>}
      {facets.data && facets.data.length === 0 && <p className="text-sm text-zinc-500">No entities were found in these documents.</p>}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {facets.data?.map((f) => <FacetCard key={f.field} facet={f} selected={params.f?.[f.field] ?? []} onPick={narrow} />)}
      </div>
    </div>
  );
}

function FacetCard({ facet, selected, onPick }: { facet: EntityFacet; selected: string[]; onPick: (field: string, value: string) => void }) {
  const most = Math.max(1, ...facet.values.map((v) => v.count));
  return (
    <section aria-label={facet.label} className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-2 flex items-baseline justify-between text-xs font-semibold uppercase tracking-wide text-zinc-500">
        <span>{facet.label}</span>
        <span className="font-normal normal-case" title={facet.field}>
          {facet.values.length}
        </span>
      </h2>
      <ul className="space-y-0.5 text-sm">
        {facet.values.map((v) => (
          <li key={v.value}>
            <button
              onClick={() => onPick(facet.field, v.value)}
              title={`show the ${v.count} document${v.count === 1 ? "" : "s"} with ${v.value}`}
              className={`relative flex w-full items-center justify-between overflow-hidden rounded px-1 py-0.5 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800 ${selected.includes(v.value) ? "font-medium" : ""}`}
            >
              <span aria-hidden className="absolute inset-y-0 left-0 bg-blue-100/70 dark:bg-blue-900/40" style={{ width: `${(v.count / most) * 100}%` }} />
              <span className="relative truncate">{facet.field.endsWith("_paths") ? v.value.split("/").pop()?.replace(/-/g, " ") : v.value}</span>
              <span className="relative ml-2 tabular-nums text-zinc-500">{v.count}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

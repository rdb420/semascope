import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api, type FacetValue } from "../api";
import { useUser } from "../components/Shell";
import { Timeline } from "../components/Timeline";

const PANEL = "rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900";
const TOP: [string, string, string][] = [
  ["people", "People", "persons_ss"],
  ["places", "Places", "places_ss"],
  ["organisations", "Organisations", "organisations_ss"],
  ["concepts", "Concepts", "concept_labels_ss"],
  ["entities", "Linked entities", "entity_labels_ss"],
];
const GAPS = [
  { value: "+10YEARS", label: "decades" },
  { value: "+1YEAR", label: "years" },
  { value: "+1MONTH", label: "months" },
];

function Values({ values, field, collection }: { values: FacetValue[]; field: string; collection: string }) {
  if (!values.length) return <p className="text-xs text-zinc-500">None found.</p>;
  const max = Math.max(...values.map((v) => v.count));
  return (
    <ul className="space-y-1">
      {values.map((v) => (
        <li key={v.value} className="grid grid-cols-[1fr_auto] items-center gap-2 text-sm">
          <Link
            to="/"
            search={{ q: "*:*", ...(collection ? { collection } : {}), f: { [field]: [v.value] } }}
            className="relative truncate rounded px-1 hover:underline"
          >
            <span className="absolute inset-y-0 left-0 rounded bg-blue-100 dark:bg-blue-950" style={{ width: `${(100 * v.count) / max}%` }} aria-hidden />
            <span className="relative">{v.value}</span>
          </Link>
          <span className="text-xs text-zinc-500">{v.count}</span>
        </li>
      ))}
    </ul>
  );
}

/** What the readable collections hold: counts, top entities and concepts, documents over time. */
export function AnalyticsPage() {
  const user = useUser();
  const [collection, setCollection] = useState("");
  const [gap, setGap] = useState("+1YEAR");
  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const overview = useQuery({
    queryKey: ["analytics", collection, gap],
    queryFn: () => api.analyticsOverview(collection || undefined, gap),
    enabled: user.isSuccess,
  });
  if (!user.isSuccess) return null;
  const o = overview.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Overview</h1>
        <select
          value={collection}
          onChange={(e) => setCollection(e.target.value)}
          aria-label="Collection"
          className="rounded border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700"
        >
          <option value="">Every collection</option>
          {collections.data?.map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.name || c.slug}
            </option>
          ))}
        </select>
        {o && <span className="text-sm text-zinc-500">{o.documents} documents</span>}
      </div>
      {overview.isError && <p className="text-red-600">{String(overview.error)}</p>}
      {o && (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <section className={PANEL}>
              <div className="mb-2 flex items-center justify-between">
                <h2 className="text-xs font-semibold uppercase text-zinc-500">Documents by date</h2>
                <div className="flex gap-1" role="group" aria-label="Bucket by">
                  {GAPS.map((g) => (
                    <button
                      key={g.value}
                      onClick={() => setGap(g.value)}
                      aria-pressed={gap === g.value}
                      className={`rounded px-1.5 text-xs ${gap === g.value ? "bg-blue-600 text-white" : "bg-zinc-100 dark:bg-zinc-800"}`}
                    >
                      {g.label}
                    </button>
                  ))}
                </div>
              </div>
              {o.modified.length ? <Timeline buckets={o.modified} /> : <p className="text-xs text-zinc-500">No dates.</p>}
            </section>
            <section className={PANEL}>
              <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Indexed per day (90 days)</h2>
              {o.indexed.length ? (
                <ul className="max-h-40 space-y-0.5 overflow-y-auto text-sm">
                  {o.indexed.map((d) => (
                    <li key={d.value} className="flex justify-between">
                      <span>{d.value}</span>
                      <span className="text-zinc-500">{d.count}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-zinc-500">Nothing indexed lately.</p>
              )}
            </section>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            {[...TOP, ["types", "Types", "content_type_group_s"], ["languages", "Languages", "language_s"]].map(([key, label, field]) => (
              <section key={key} className={PANEL}>
                <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">{label}</h2>
                <Values values={(o.top[key] ?? o.breakdown[key]) || []} field={field} collection={collection} />
              </section>
            ))}
          </div>
          {!collection && o.per_collection.length > 1 && (
            <section className={PANEL}>
              <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">By collection</h2>
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-zinc-500">
                  <tr>
                    <th className="py-1">Collection</th>
                    <th>Documents</th>
                    <th>Top concepts</th>
                    <th>Top entities</th>
                  </tr>
                </thead>
                <tbody>
                  {o.per_collection.map((c) => (
                    <tr key={c.collection} className="border-t border-zinc-100 dark:border-zinc-800">
                      <td className="py-1">
                        <button className="text-blue-700 hover:underline dark:text-blue-400" onClick={() => setCollection(c.collection)}>
                          {c.collection}
                        </button>
                      </td>
                      <td>{c.documents}</td>
                      <td className="text-zinc-600 dark:text-zinc-400">{c.concepts.map((v) => v.value).slice(0, 5).join(", ")}</td>
                      <td className="text-zinc-600 dark:text-zinc-400">{c.entities.map((v) => v.value).slice(0, 5).join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </div>
  );
}

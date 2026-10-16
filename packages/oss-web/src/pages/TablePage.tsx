import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api, originalUrl, views, type FacetValue, type SearchHit, type TableStats } from "../api";
import { useUser } from "../components/Shell";
import { SearchSummary, ViewLinks } from "../components/ViewLinks";
import { toSearchIn, type ViewParams } from "../viewParams";

const PAGE_SIZE = 50;

type Column = {
  key: string;
  label: string;
  value: (h: SearchHit) => string | number | null;
  render?: (h: SearchHit) => ReactNode;
  /** Solr sorts over every match; other columns sort the rows on this page. */
  server?: { asc?: string; desc?: string };
  numeric?: boolean;
};

const COLUMNS: Column[] = [
  { key: "title", label: "Title", value: (h) => h.title.toLowerCase(), server: { asc: "title_s asc" } },
  { key: "collection", label: "Collection", value: (h) => h.collection },
  { key: "type", label: "Type", value: (h) => h.content_type_group },
  { key: "format", label: "Format", value: (h) => h.media_type },
  { key: "language", label: "Language", value: (h) => h.language },
  { key: "pages", label: "Pages", value: (h) => h.page_count, numeric: true },
  { key: "size", label: "Size", value: (h) => h.size, numeric: true, server: { desc: "size_l desc" }, render: (h) => (h.size ? kb(h.size) : "") },
  { key: "modified", label: "Modified", value: (h) => h.modified_at, server: { desc: "modified_dt desc", asc: "modified_dt asc" }, render: (h) => day(h.modified_at) },
  { key: "indexed", label: "Indexed", value: (h) => h.indexed_at, server: { desc: "indexed_dt desc" }, render: (h) => day(h.indexed_at) },
];

function kb(n: number): string {
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

function day(v: string | null): string {
  return v ? v.slice(0, 10) : "";
}

/** The table view of a search: one row per hit, sortable columns and column statistics. */
export function TablePage() {
  const params = useSearch({ from: "/table" });
  const navigate = useNavigate({ from: "/table" });
  const user = useUser();
  const page = params.page ?? 1;
  const payload = toSearchIn(params, { facets: [], page, page_size: PAGE_SIZE, sort: params.sort ?? null });
  const results = useQuery({ queryKey: ["table", params], queryFn: () => api.search(payload), enabled: user.isSuccess, placeholderData: (prev) => prev });
  const stats = useQuery({
    queryKey: ["table-stats", { ...params, sort: undefined, page: undefined }],
    queryFn: () => views.tableStats(toSearchIn(params)),
    enabled: user.isSuccess,
  });
  const [local, setLocal] = useState<{ key: string; dir: 1 | -1 } | null>(null);

  const rows = useMemo(() => {
    const hits = results.data?.hits ?? [];
    if (!local) return hits;
    const col = COLUMNS.find((c) => c.key === local.key)!;
    return [...hits].sort((a, b) => {
      const x = col.value(a);
      const y = col.value(b);
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * local.dir;
    });
  }, [results.data, local]);

  function sortBy(col: Column) {
    const server = col.server;
    if (server) {
      const current = params.sort;
      const next = current === server.desc && server.asc ? server.asc : current === server.asc && server.desc ? server.desc : (server.desc ?? server.asc);
      setLocal(null);
      navigate({ search: (prev: ViewParams) => ({ ...prev, sort: next, page: undefined }) });
    } else {
      setLocal((prev) => ({ key: col.key, dir: prev?.key === col.key && prev.dir === 1 ? -1 : 1 }));
    }
  }

  function indicator(col: Column): string {
    if (local?.key === col.key) return local.dir === 1 ? " ▲" : " ▼";
    if (!local && col.server && params.sort) {
      if (params.sort === col.server.asc) return " ▲";
      if (params.sort === col.server.desc) return " ▼";
    }
    return "";
  }

  if (!user.isSuccess) return null;
  const data = results.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <h1 className="mr-2 text-xl font-semibold">Table</h1>
        <ViewLinks params={params} current="table" />
        {params.sort && (
          <button onClick={() => navigate({ search: (prev: ViewParams) => ({ ...prev, sort: undefined, page: undefined }) })} className="text-blue-700 hover:underline dark:text-blue-400">
            sort by relevance
          </button>
        )}
      </div>
      <SearchSummary params={params} />
      {stats.data && <StatsStrip stats={stats.data} />}
      {results.isError && <p className="text-red-600">{String(results.error)}</p>}
      <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-zinc-200 text-xs uppercase text-zinc-500 dark:border-zinc-800">
            <tr>
              {COLUMNS.map((col) => (
                <th key={col.key} className={`px-3 py-2 ${col.numeric ? "text-right" : ""}`} aria-sort={indicator(col) === " ▲" ? "ascending" : indicator(col) === " ▼" ? "descending" : "none"}>
                  <button onClick={() => sortBy(col)} className="uppercase hover:underline" title={col.server ? "sort every match" : "sort the rows on this page"}>
                    {col.label}
                    {indicator(col)}
                  </button>
                </th>
              ))}
              <th className="px-3 py-2">Original</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((h) => (
              <tr key={h.id} className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800/50">
                {COLUMNS.map((col) => (
                  <td key={col.key} className={`px-3 py-1.5 ${col.numeric ? "text-right tabular-nums" : ""} ${col.key === "title" ? "max-w-md" : "whitespace-nowrap"}`}>
                    {col.key === "title" ? (
                      <Link to="/documents/$id" params={{ id: h.id }} search={params.q ? { q: params.q } : {}} className="line-clamp-2 text-blue-700 hover:underline dark:text-blue-400">
                        {h.title}
                      </Link>
                    ) : col.render ? (
                      col.render(h)
                    ) : (
                      (col.value(h) ?? "")
                    )}
                  </td>
                ))}
                <td className="whitespace-nowrap px-3 py-1.5">
                  <a href={originalUrl(h.id)} target="_blank" rel="noreferrer" className="text-blue-700 hover:underline dark:text-blue-400">
                    open
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && data.hits.length === 0 && <p className="p-4 text-sm text-zinc-500">No documents match.</p>}
      </div>
      {data && (
        <nav className="flex items-center gap-3 text-sm">
          <span className="text-zinc-500">{data.total.toLocaleString()} documents</span>
          {pages > 1 && (
            <>
              <button disabled={page <= 1} onClick={() => navigate({ search: (prev: ViewParams) => ({ ...prev, page: page - 1 > 1 ? page - 1 : undefined }) })} className="rounded border px-3 py-1 disabled:opacity-40">
                Previous
              </button>
              <span>
                Page {page} of {pages}
              </span>
              <button disabled={page >= pages} onClick={() => navigate({ search: (prev: ViewParams) => ({ ...prev, page: page + 1 }) })} className="rounded border px-3 py-1 disabled:opacity-40">
                Next
              </button>
            </>
          )}
        </nav>
      )}
    </div>
  );
}

function StatsStrip({ stats }: { stats: TableStats }) {
  const top = (values: FacetValue[]) => values.slice(0, 4).map((v) => `${v.value} ${v.count}`).join(" · ");
  return (
    <dl aria-label="Column statistics" className="grid grid-cols-2 gap-3 rounded-lg border border-zinc-200 bg-white p-3 text-sm md:grid-cols-5 dark:border-zinc-800 dark:bg-zinc-900">
      <Stat k="Documents" v={stats.total.toLocaleString()} />
      <Stat k="Size" v={stats.size.count ? `${kb(stats.size.sum ?? 0)} in all · ${kb(stats.size.mean ?? 0)} mean` : "—"} />
      <Stat k="Pages" v={stats.pages.count ? `${Math.round(stats.pages.mean ?? 0)} mean · ${stats.pages.max ?? 0} most` : "—"} />
      <Stat k="Modified" v={stats.modified.count ? `${day(stats.modified.min)} – ${day(stats.modified.max)}` : "—"} />
      <Stat k="Types · languages" v={[top(stats.types), top(stats.languages)].filter(Boolean).join(" | ") || "—"} />
    </dl>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs uppercase text-zinc-500">{k}</dt>
      <dd className="truncate" title={v}>
        {v}
      </dd>
    </div>
  );
}

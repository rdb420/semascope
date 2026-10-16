import { useCallback, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, originalUrl, type SearchHit } from "../api";
import { Timeline } from "../components/Timeline";
import { useUser } from "../components/Shell";
import { ViewLinks } from "../components/ViewLinks";
import { SearchBox } from "../components/SearchBox";
import { Facet, SelectedFilters } from "../components/FacetPanel";
import {
  OPERATORS,
  SORTS,
  barRange,
  clean,
  facetTitle,
  fromSaved,
  histogramGap,
  toPayload,
  toSaved,
  toggleValue,
  type SearchParams,
  sortKey,
  type SortKey,
} from "../searchState";

export { searchSearchSchema, type SearchParams } from "../searchState";

const FACETS = [
  "content_type_group_s",
  "language_s",
  "media_type_s",
  "source_id_s",
  "entity_labels_ss",
  "persons_ss",
  "places_ss",
  "organisations_ss",
  "emails_ss",
  "urls_ss",
  "phones_ss",
  "money_ss",
  "hashtags_ss",
];
const FACET_LIMIT = 20;
const select = "rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900";

export function SearchPage() {
  const user = useUser();
  const navigate = useNavigate({ from: "/" });
  const params = useSearch({ from: "/" }) as SearchParams;
  const [draft, setDraft] = useState(params.q ?? "");
  const [more, setMore] = useState<string[]>([]);
  const [exportError, setExportError] = useState<string | null>(null);

  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const suggestions = useQuery({
    queryKey: ["tag-query", params.q],
    enabled: user.isSuccess && !!params.q && params.q !== "*:*",
    queryFn: () => api.tagQuery(params.q ?? ""),
    staleTime: 60_000,
  });
  const view = params.view ?? "list";
  const payload = toPayload(params, FACETS, more);
  const results = useQuery({
    queryKey: ["search", params, more],
    enabled: user.isSuccess,
    queryFn: () => api.search(payload),
    placeholderData: (prev) => prev,
  });
  const termsQ = useQuery({ queryKey: ["terms", params], queryFn: () => api.terms(payload), enabled: user.isSuccess && view === "terms" });
  const qc = useQueryClient();
  const saved = useQuery({ queryKey: ["saved"], queryFn: api.savedSearches, enabled: user.isSuccess });
  const save = useMutation({
    mutationFn: (name: string) => api.saveSearch({ name, query: toSaved(params, FACETS), alert: false }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved"] }),
  });
  const unsave = useMutation({ mutationFn: (id: number) => api.deleteSavedSearch(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["saved"] }) });

  function update(next: Partial<SearchParams>) {
    navigate({ search: (prev: SearchParams) => clean({ ...prev, ...next }) });
  }
  function submit(e?: FormEvent, q: string = draft) {
    e?.preventDefault();
    update({ q, page: undefined });
  }
  function toggle(field: string, value: string) {
    // choosing a value takes it off the excluded list, and the other way round
    update({ f: toggleValue(params.f, field, value), x: dropValue(params.x, field, value), page: undefined });
  }
  function exclude(field: string, value: string) {
    update({ x: toggleValue(params.x, field, value), f: dropValue(params.f, field, value), page: undefined });
  }
  const gap = histogramGap(params.from, params.to);
  const pick = useCallback(
    (bucket: string) => {
      const r = barRange(bucket, gap);
      navigate({ search: (prev: SearchParams) => clean({ ...prev, from: r.from, to: r.to, page: undefined }) });
    },
    [gap, navigate],
  );
  async function exportCsv() {
    setExportError(null);
    try {
      const blob = await api.searchCsv({ ...payload, page: 1 });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "semascope-search.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(`Export failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  if (!user.isSuccess) return null;
  const data = results.data;
  const pageSize = payload.page_size ?? 10;
  const pages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;
  const page = params.page ?? 1;
  const sort: SortKey = sortKey(params.sort);

  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="flex flex-wrap gap-2" role="search">
        <select
          value={params.collection ?? ""}
          onChange={(e) => update({ collection: e.target.value || undefined, page: undefined })}
          className="rounded border border-zinc-300 bg-white px-2 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          aria-label="Collection"
        >
          <option value="">All collections</option>
          {collections.data?.map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.name} ({c.document_count})
            </option>
          ))}
        </select>
        <SearchBox
          value={draft}
          onChange={setDraft}
          onSubmit={(q) => submit(undefined, q)}
          collection={params.collection}
          placeholder="Search… (phrases in quotes, field:value, AND / OR / NOT, exact:word)"
        />
        <select
          value={params.mode ?? "keyword"}
          onChange={(e) => update({ mode: e.target.value === "keyword" ? undefined : (e.target.value as "semantic" | "hybrid"), page: undefined })}
          title="keyword: words and phrases · semantic: meaning, by passage · hybrid: both, fused"
          aria-label="Search mode"
          className="rounded border border-zinc-300 bg-white px-2 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        >
          <option value="keyword">keyword</option>
          <option value="semantic">semantic</option>
          <option value="hybrid">hybrid</option>
        </select>
        <button className="rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700">Search</button>
      </form>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <label className="flex items-center gap-1">
          <span className="text-zinc-500">Sort</span>
          <select value={sort} onChange={(e) => update({ sort: e.target.value as SortKey, page: undefined })} className={select} aria-label="Sort">
            {(Object.keys(SORTS) as SortKey[]).map((k) => (
              <option key={k} value={k}>
                {SORTS[k].label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1" title="How many of the words a document needs (docs/search.md)">
          <span className="text-zinc-500">Match</span>
          <select value={params.op ?? ""} onChange={(e) => update({ op: (e.target.value || undefined) as SearchParams["op"], page: undefined })} className={select} aria-label="Match">
            {Object.entries(OPERATORS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1" title="Also find other forms of the words (run, runs, running) in each document's language">
          <input type="checkbox" checked={params.stem !== false} onChange={(e) => update({ stem: e.target.checked ? undefined : false, page: undefined })} />
          other word forms
        </label>
        <span className="mx-1 text-zinc-300 dark:text-zinc-700">|</span>
        {(["list", "media", "terms"] as const).map((v) => (
          <button key={v} onClick={() => update({ view: v === "list" ? undefined : v, page: undefined })} className={`rounded border px-2 py-0.5 ${view === v ? "bg-blue-100 dark:bg-blue-900/40" : ""}`}>
            {v}
          </button>
        ))}
        <ViewLinks params={{ q: params.q, collection: params.collection, f: params.f, mode: params.mode }} />
        <button
          onClick={() => {
            const name = window.prompt("Name this search");
            if (name) save.mutate(name);
          }}
          className="rounded border px-2 py-0.5"
        >
          Save search
        </button>
        <button onClick={exportCsv} className="rounded border px-2 py-0.5">
          Export CSV
        </button>
        {saved.data && saved.data.length > 0 && (
          <span className="ml-2 flex flex-wrap items-center gap-1">
            <span className="text-zinc-500">Saved:</span>
            {saved.data.map((s) => (
              <span key={s.id} className="flex items-center gap-1 rounded-full border px-2 py-0.5">
                <button
                  onClick={() => {
                    const p = fromSaved(s.query);
                    setDraft(p.q ?? "");
                    navigate({ search: p });
                  }}
                  className="hover:underline"
                >
                  {s.name}
                </button>
                <button onClick={() => unsave.mutate(s.id)} className="text-zinc-400 hover:text-red-600" title="delete">
                  ×
                </button>
              </span>
            ))}
          </span>
        )}
      </div>
      {exportError && (
        <p role="alert" className="text-sm text-red-600">
          {exportError}
        </p>
      )}
      {save.isError && (
        <p role="alert" className="text-sm text-red-600">
          Could not save the search: {String(save.error)}
        </p>
      )}
      <SelectedFilters
        f={params.f}
        x={params.x}
        from={params.from}
        to={params.to}
        onRemove={(k, v) => update({ f: toggleValue(params.f, k, v), page: undefined })}
        onRemoveExcluded={(k, v) => update({ x: toggleValue(params.x, k, v), page: undefined })}
        onClearDates={() => update({ from: undefined, to: undefined, page: undefined })}
        onClearAll={() => update({ f: undefined, x: undefined, from: undefined, to: undefined, page: undefined })}
      />
      {suggestions.data && suggestions.data.length > 0 && (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-zinc-500">Your query names:</span>
          {suggestions.data.map((s) => (
            <Link
              key={s.id}
              to="/concepts"
              search={{ iri: s.id }}
              className="rounded-full border border-blue-300 bg-blue-50 px-2 py-0.5 text-blue-800 hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200"
              title={s.phrase}
            >
              {s.name}
              {s.type[0]?.name ? <span className="ml-1 text-xs text-zinc-500">{s.type[0].name}</span> : null}
            </Link>
          ))}
        </p>
      )}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-[16rem_1fr]">
        <aside className="space-y-5" aria-label="Facets">
          {data &&
            Object.entries(data.facets)
              .filter(([, values]) => values.length)
              .map(([field, values]) => (
                <Facet
                  key={field}
                  field={field}
                  values={values}
                  selected={params.f?.[field] ?? []}
                  excluded={params.x?.[field] ?? []}
                  expanded={more.includes(field)}
                  limit={more.includes(field) ? 100 : FACET_LIMIT}
                  onToggle={toggle}
                  onExclude={exclude}
                  onMore={(f, wanted) => setMore((m) => (wanted ? [...new Set([...m, f])] : m.filter((x) => x !== f)))}
                />
              ))}
        </aside>
        <section>
          {results.isError && <p className="text-red-600">{String(results.error)}</p>}
          {data && (
            <p className="mb-3 text-sm text-zinc-500">
              {data.total.toLocaleString()} result{data.total === 1 ? "" : "s"} · {data.took_ms} ms
              {!params.q && sort === "relevance" ? " · newest first" : ""}
            </p>
          )}
          {data && (data.histogram.length > 1 || params.from) && view !== "terms" && (
            <div className="mb-3 rounded-lg border border-zinc-200 bg-white p-2 dark:border-zinc-800 dark:bg-zinc-900">
              <p className="mb-1 flex items-center gap-2 text-xs text-zinc-500">
                Modified, by {gap === "+1YEAR" ? "year" : gap === "+1MONTH" ? "month" : "day"} · click a bar to zoom in
                {params.from && (
                  <button onClick={() => update({ from: undefined, to: undefined, page: undefined })} className="text-blue-700 hover:underline dark:text-blue-400">
                    all dates
                  </button>
                )}
              </p>
              <Timeline buckets={data.histogram} gap={gap} onPick={pick} />
            </div>
          )}
          {view === "terms" && (
            <ul className="flex flex-wrap gap-2">
              {termsQ.data?.map((t) => (
                <li key={t.term}>
                  <button
                    onClick={() => {
                      setDraft(t.term);
                      update({ q: t.term, page: undefined, view: undefined });
                    }}
                    className="rounded-full border border-zinc-300 px-2 py-0.5 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
                    style={{ fontSize: `${Math.min(22, 11 + Math.log2(t.count))}px` }}
                    title={`${t.count} occurrences in ${t.docs} documents`}
                  >
                    {t.term}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {view === "media" && (
            <ul className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {data?.hits.map((hit) => (
                <li key={hit.id} className="rounded-lg border border-zinc-200 bg-white p-2 dark:border-zinc-800 dark:bg-zinc-900">
                  <Link to="/documents/$id" params={{ id: hit.id }}>
                    {hit.preview_pages > 0 ? (
                      <img src={`/api/documents/${encodeURIComponent(hit.id)}/preview/1`} alt="" loading="lazy" className="h-36 w-full rounded object-cover" />
                    ) : (
                      <div className="flex h-36 items-center justify-center rounded bg-zinc-100 text-xs text-zinc-500 dark:bg-zinc-800">no preview</div>
                    )}
                    <p className="mt-1 truncate text-xs">{hit.title}</p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {view === "list" && (
            <ol className="space-y-4">
              {data?.hits.map((hit) => (
                <Hit key={hit.id} hit={hit} q={params.q} onFilter={(field, value) => update({ f: addValue(params.f, field, value), page: undefined })} />
              ))}
            </ol>
          )}
          {data && pages > 1 && (
            <nav className="mt-6 flex items-center gap-3 text-sm" aria-label="Pages">
              <button disabled={page <= 1} onClick={() => update({ page: page - 1 })} className="rounded border px-3 py-1 disabled:opacity-40">
                Previous
              </button>
              <span>
                Page {page} of {pages}
              </span>
              <button disabled={page >= pages} onClick={() => update({ page: page + 1 })} className="rounded border px-3 py-1 disabled:opacity-40">
                Next
              </button>
            </nav>
          )}
        </section>
      </div>
    </div>
  );
}

function dropValue(map: SearchParams["f"], field: string, value: string): SearchParams["f"] {
  if (!map?.[field]?.includes(value)) return map;
  return toggleValue(map, field, value);
}

function addValue(map: SearchParams["f"], field: string, value: string): SearchParams["f"] {
  if (map?.[field]?.includes(value)) return map;
  return toggleValue(map, field, value);
}

function Hit({ hit, q, onFilter }: { hit: SearchHit; q?: string; onFilter: (field: string, value: string) => void }) {
  const carry = q && q !== "*:*" ? { q } : {}; // the document page highlights the query
  // a few of the document's entities and concepts, each name once, the first field naming it
  const seen = new Set<string>();
  const chips = Object.entries(hit.entities ?? {})
    .flatMap(([field, values]) => values.map((v) => [field, v] as const))
    .filter(([, v]) => !seen.has(v) && seen.add(v) !== undefined)
    .slice(0, 8);
  return (
    <li className="flex gap-4 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      {hit.preview_pages > 0 && (
        <Link to="/documents/$id" params={{ id: hit.id }} search={carry} className="shrink-0">
          <img
            src={`/api/documents/${encodeURIComponent(hit.id)}/preview/1`}
            alt=""
            loading="lazy"
            className="h-28 w-20 rounded border border-zinc-200 object-cover object-top dark:border-zinc-700"
          />
        </Link>
      )}
      <div className="min-w-0 flex-1">
        <Link to="/documents/$id" params={{ id: hit.id }} search={carry} className="font-medium text-blue-700 hover:underline dark:text-blue-400">
          {hit.title}
        </Link>
        <p className="truncate text-xs text-zinc-500">{hit.uri}</p>
        <ul className="mt-2 space-y-1 text-sm">
          {hit.snippets.map((s, i) => (
            <li key={i} dangerouslySetInnerHTML={{ __html: sanitiseSnippet(s) }} />
          ))}
          {hit.passages?.map((p, i) => (
            <li key={`p${i}`} className="border-l-2 border-blue-300 pl-2 text-zinc-600 dark:text-zinc-300">
              {p}
            </li>
          ))}
        </ul>
        {chips.length > 0 && (
          <p className="mt-2 flex flex-wrap gap-1 text-xs">
            {chips.map(([field, value]) => (
              <button
                key={`${field}${value}`}
                onClick={() => onFilter(field, value)}
                title={`${facetTitle(field)}: show only documents with ${value}`}
                className="rounded-full border border-zinc-300 px-2 py-0.5 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              >
                {value}
              </button>
            ))}
          </p>
        )}
        <p className="mt-2 flex flex-wrap gap-3 text-xs text-zinc-500">
          {hit.modified_at && <span title="modified">{hit.modified_at.slice(0, 10)}</span>}
          {hit.content_type_group && <span>{hit.content_type_group}</span>}
          {hit.media_type && <span>{hit.media_type}</span>}
          {hit.language && <span>{hit.language}</span>}
          {hit.page_count ? <span>{hit.page_count} pages</span> : null}
          {hit.size ? <span>{(hit.size / 1024).toFixed(0)} KB</span> : null}
          <a href={originalUrl(hit.id)} target="_blank" rel="noreferrer" className="text-blue-700 hover:underline dark:text-blue-400">
            open original
          </a>
        </p>
      </div>
    </li>
  );
}

// Solr snippets contain only our own <mark> tags; everything else is escaped text.
export function sanitiseSnippet(s: string): string {
  const escaped = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return escaped.replace(/&lt;mark&gt;/g, "<mark>").replace(/&lt;\/mark&gt;/g, "</mark>");
}


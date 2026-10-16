import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type FacetValue, type SearchHit, type SearchIn } from "../api";
import { Timeline } from "../components/Timeline";
import { useUser } from "../components/Shell";
import { ViewLinks } from "../components/ViewLinks";
import { originalUrl } from "../api";

export type SearchParams = {
  q?: string;
  collection?: string;
  page?: number;
  f?: Record<string, string[]>;
  view?: "list" | "media" | "terms";
  mode?: "semantic" | "hybrid";
};

export function searchSearchSchema(raw: Record<string, unknown>): SearchParams {
  const out: SearchParams = {};
  if (typeof raw.q === "string" && raw.q) out.q = raw.q;
  if (typeof raw.collection === "string" && raw.collection) out.collection = raw.collection;
  if (typeof raw.page === "number" && raw.page > 1) out.page = raw.page;
  if (raw.f && typeof raw.f === "object") out.f = raw.f as Record<string, string[]>;
  if (raw.view === "media" || raw.view === "terms") out.view = raw.view;
  if (raw.mode === "semantic" || raw.mode === "hybrid") out.mode = raw.mode;
  return out;
}

const FACET_LABELS: Record<string, string> = {
  persons_ss: "People",
  organisations_ss: "Organisations",
  places_ss: "Places",
  entity_labels_ss: "Linked entities",
  content_type_group_s: "Type",
  language_s: "Language",
  media_type_s: "Format",
  source_id_s: "Source",
  emails_ss: "Email addresses",
  urls_ss: "Links",
  money_ss: "Amounts",
  hashtags_ss: "Hashtags",
  concept_labels_ss: "Concepts",
};
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
  "money_ss",
  "hashtags_ss",
];
const PAGE_SIZE = 10;

export function SearchPage() {
  const user = useUser();
  const navigate = useNavigate({ from: "/" });
  const params = useSearchParamsSafe();
  const [draft, setDraft] = useState(params.q ?? "");
  const filters = params.f ?? {};

  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const suggestions = useQuery({
    queryKey: ["tag-query", params.q],
    enabled: user.isSuccess && !!params.q && params.q !== "*:*",
    queryFn: () => api.tagQuery(params.q ?? ""),
    staleTime: 60_000,
  });
  const view = params.view ?? "list";
  const payload: SearchIn = {
    q: params.q || "*:*",
    collection: params.collection ?? null,
    filters: view === "media" ? { ...filters, content_type_group_s: ["image"] } : filters,
    facets: FACETS,
    page: params.page ?? 1,
    page_size: view === "media" ? 40 : PAGE_SIZE,
    histogram: "modified_dt",
    histogram_gap: "+1YEAR",
    mode: params.mode ?? "keyword",
  };
  const results = useQuery({
    queryKey: ["search", params],
    enabled: user.isSuccess,
    queryFn: () => api.search(payload),
    placeholderData: (prev) => prev,
  });
  const termsQ = useQuery({ queryKey: ["terms", params], queryFn: () => api.terms(payload), enabled: user.isSuccess && view === "terms" });
  const qc = useQueryClient();
  const saved = useQuery({ queryKey: ["saved"], queryFn: api.savedSearches, enabled: user.isSuccess });
  const save = useMutation({
    mutationFn: (name: string) => api.saveSearch({ name, query: { ...payload, page: 1 }, alert: false }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved"] }),
  });
  const unsave = useMutation({ mutationFn: (id: number) => api.deleteSavedSearch(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["saved"] }) });

  function update(next: Partial<SearchParams>) {
    navigate({ search: (prev: SearchParams) => clean({ ...prev, ...next }) });
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    update({ q: draft, page: undefined });
  }
  function toggle(field: string, value: string) {
    const current = new Set(filters[field] ?? []);
    if (current.has(value)) current.delete(value);
    else current.add(value);
    const f = { ...filters, [field]: [...current] };
    if (f[field].length === 0) delete f[field];
    update({ f: Object.keys(f).length ? f : undefined, page: undefined });
  }

  if (!user.isSuccess) return null;
  const data = results.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const page = params.page ?? 1;

  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="flex gap-2">
        <select
          value={params.collection ?? ""}
          onChange={(e) => update({ collection: e.target.value || undefined, page: undefined })}
          className="rounded border border-zinc-300 bg-white px-2 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        >
          <option value="">All collections</option>
          {collections.data?.map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.name} ({c.document_count})
            </option>
          ))}
        </select>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Search… (phrases in quotes, field:value, AND / OR / NOT)"
          className="flex-1 rounded border border-zinc-300 bg-white px-3 py-2 dark:border-zinc-700 dark:bg-zinc-900"
        />
        <select
          value={params.mode ?? "keyword"}
          onChange={(e) => update({ mode: e.target.value === "keyword" ? undefined : (e.target.value as "semantic" | "hybrid"), page: undefined })}
          title="keyword: words and phrases · semantic: meaning, by passage · hybrid: both, fused"
          className="rounded border border-zinc-300 bg-white px-2 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        >
          <option value="keyword">keyword</option>
          <option value="semantic">semantic</option>
          <option value="hybrid">hybrid</option>
        </select>
        <button className="rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700">Search</button>
      </form>

      <div className="flex flex-wrap items-center gap-2 text-sm">
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
        <a
          href="#"
          onClick={async (e) => {
            e.preventDefault();
            const res = await fetch("/api/search/export.csv", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] ?? "" }, body: JSON.stringify(payload), credentials: "same-origin" });
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "semascope-search.csv";
            a.click();
            URL.revokeObjectURL(url);
          }}
          className="rounded border px-2 py-0.5"
        >
          Export CSV
        </a>
        {saved.data && saved.data.length > 0 && (
          <span className="ml-2 flex flex-wrap items-center gap-1">
            <span className="text-zinc-500">Saved:</span>
            {saved.data.map((s) => (
              <span key={s.id} className="flex items-center gap-1 rounded-full border px-2 py-0.5">
                <button onClick={() => navigate({ search: clean({ q: s.query.q, collection: s.query.collection ?? undefined, f: s.query.filters }) })} className="hover:underline">
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
        <aside className="space-y-5">
          {data &&
            Object.entries(data.facets)
              .filter(([, values]) => values.length)
              .map(([field, values]) => (
                <Facet key={field} field={field} values={values} selected={filters[field] ?? []} onToggle={toggle} />
              ))}
        </aside>
        <section>
          {results.isError && <p className="text-red-600">{String(results.error)}</p>}
          {data && (
            <p className="mb-3 text-sm text-zinc-500">
              {data.total.toLocaleString()} result{data.total === 1 ? "" : "s"} · {data.took_ms} ms
            </p>
          )}
          {data && data.histogram.length > 1 && view !== "terms" && (
            <div className="mb-3 rounded-lg border border-zinc-200 bg-white p-2 dark:border-zinc-800 dark:bg-zinc-900">
              <Timeline buckets={data.histogram} />
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
                      <img src={`/api/documents/${hit.id}/preview/1`} alt="" loading="lazy" className="h-36 w-full rounded object-cover" />
                    ) : (
                      <div className="flex h-36 items-center justify-center rounded bg-zinc-100 text-xs text-zinc-500 dark:bg-zinc-800">no preview</div>
                    )}
                    <p className="mt-1 truncate text-xs">{hit.title}</p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {view === "list" && <ol className="space-y-4">{data?.hits.map((hit) => <Hit key={hit.id} hit={hit} q={params.q} />)}</ol>}
          {data && pages > 1 && (
            <nav className="mt-6 flex items-center gap-3 text-sm">
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

function facetTitle(field: string): string {
  if (FACET_LABELS[field]) return FACET_LABELS[field];
  if (field.endsWith("_paths")) return field.slice(0, -6).replace(/-/g, " ");
  if (field.endsWith("_entities_ss")) {
    const cls = field.replace(/^x_/, "").slice(0, -"_entities_ss".length).replace(/_/g, " ");
    return cls.charAt(0).toUpperCase() + cls.slice(1) + " (found)";
  }
  return field;
}

type TreeNode = { value: string; label: string; count: number; children: TreeNode[] };

function buildTree(values: FacetValue[]): TreeNode[] {
  const roots: TreeNode[] = [];
  const byPath = new Map<string, TreeNode>();
  for (const v of [...values].sort((a, b) => a.value.localeCompare(b.value))) {
    const parts = v.value.split("/");
    const node: TreeNode = { value: v.value, label: parts[parts.length - 1].replace(/-/g, " "), count: v.count, children: [] };
    byPath.set(v.value, node);
    const parent = parts.length > 1 ? byPath.get(parts.slice(0, -1).join("/")) : undefined;
    (parent ? parent.children : roots).push(node);
  }
  return roots;
}

function Facet({
  field,
  values,
  selected,
  onToggle,
}: {
  field: string;
  values: FacetValue[];
  selected: string[];
  onToggle: (field: string, value: string) => void;
}) {
  if (field.endsWith("_paths")) {
    const tree = buildTree(values);
    const render = (nodes: TreeNode[], depth: number) =>
      nodes.map((n) => {
        const on = selected.includes(n.value);
        return (
          <li key={n.value}>
            <button
              onClick={() => onToggle(field, n.value)}
              style={{ paddingLeft: `${depth * 0.75 + 0.25}rem` }}
              className={`flex w-full items-center justify-between rounded py-0.5 pr-1 text-left capitalize hover:bg-zinc-200 dark:hover:bg-zinc-800 ${on ? "bg-blue-100 font-medium dark:bg-blue-900/40" : ""}`}
            >
              <span className="truncate">{n.label}</span>
              <span className="ml-2 text-zinc-500">{n.count}</span>
            </button>
            {n.children.length > 0 && <ul>{render(n.children, depth + 1)}</ul>}
          </li>
        );
      });
    return (
      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">{facetTitle(field)}</h3>
        <ul className="space-y-0.5 text-sm">{render(tree, 0)}</ul>
      </div>
    );
  }
  return (
    <div>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">{facetTitle(field)}</h3>
      <ul className="space-y-0.5 text-sm">
        {values.map((v) => {
          const on = selected.includes(v.value);
          return (
            <li key={v.value}>
              <button
                onClick={() => onToggle(field, v.value)}
                className={`flex w-full items-center justify-between rounded px-1 py-0.5 text-left hover:bg-zinc-200 dark:hover:bg-zinc-800 ${on ? "bg-blue-100 font-medium dark:bg-blue-900/40" : ""}`}
              >
                <span className="truncate">{v.value}</span>
                <span className="ml-2 text-zinc-500">{v.count}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Hit({ hit, q }: { hit: SearchHit; q?: string }) {
  const carry = q && q !== "*:*" ? { q } : {}; // the document page highlights the query
  return (
    <li className="flex gap-4 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      {hit.preview_pages > 0 && (
        <Link to="/documents/$id" params={{ id: hit.id }} search={carry} className="shrink-0">
          <img
            src={`/api/documents/${hit.id}/preview/1`}
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
      <p className="mt-2 flex flex-wrap gap-3 text-xs text-zinc-500">
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

function clean(p: SearchParams): SearchParams {
  const out: SearchParams = {};
  if (p.q) out.q = p.q;
  if (p.collection) out.collection = p.collection;
  if (p.page && p.page > 1) out.page = p.page;
  if (p.f && Object.keys(p.f).length) out.f = p.f;
  if (p.view && p.view !== "list") out.view = p.view;
  if (p.mode) out.mode = p.mode;
  return out;
}

import { useSearch } from "@tanstack/react-router";
function useSearchParamsSafe(): SearchParams {
  return useSearch({ from: "/" });
}

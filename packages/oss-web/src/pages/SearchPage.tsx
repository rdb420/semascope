import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api, type FacetValue, type SearchHit } from "../api";
import { useUser } from "../components/Shell";

export type SearchParams = {
  q?: string;
  collection?: string;
  page?: number;
  f?: Record<string, string[]>;
};

export function searchSearchSchema(raw: Record<string, unknown>): SearchParams {
  const out: SearchParams = {};
  if (typeof raw.q === "string" && raw.q) out.q = raw.q;
  if (typeof raw.collection === "string" && raw.collection) out.collection = raw.collection;
  if (typeof raw.page === "number" && raw.page > 1) out.page = raw.page;
  if (raw.f && typeof raw.f === "object") out.f = raw.f as Record<string, string[]>;
  return out;
}

const FACET_LABELS: Record<string, string> = {
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
const FACETS = ["content_type_group_s", "language_s", "media_type_s", "source_id_s", "emails_ss", "money_ss", "hashtags_ss"];
const PAGE_SIZE = 10;

export function SearchPage() {
  const user = useUser();
  const navigate = useNavigate({ from: "/" });
  const params = useSearchParamsSafe();
  const [draft, setDraft] = useState(params.q ?? "");
  const filters = params.f ?? {};

  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const results = useQuery({
    queryKey: ["search", params],
    enabled: user.isSuccess,
    queryFn: () =>
      api.search({
        q: params.q || "*:*",
        collection: params.collection ?? null,
        filters,
        facets: FACETS,
        page: params.page ?? 1,
        page_size: PAGE_SIZE,
      }),
    placeholderData: (prev) => prev,
  });

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
        <button className="rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700">Search</button>
      </form>

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
          <ol className="space-y-4">{data?.hits.map((hit) => <Hit key={hit.id} hit={hit} />)}</ol>
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
  return (
    <div>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">{FACET_LABELS[field] ?? field}</h3>
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

function Hit({ hit }: { hit: SearchHit }) {
  return (
    <li className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <Link to="/documents/$id" params={{ id: hit.id }} className="font-medium text-blue-700 hover:underline dark:text-blue-400">
        {hit.title}
      </Link>
      <p className="truncate text-xs text-zinc-500">{hit.uri}</p>
      <ul className="mt-2 space-y-1 text-sm">
        {hit.snippets.map((s, i) => (
          <li key={i} dangerouslySetInnerHTML={{ __html: sanitiseSnippet(s) }} />
        ))}
      </ul>
      <p className="mt-2 flex flex-wrap gap-3 text-xs text-zinc-500">
        {hit.content_type_group && <span>{hit.content_type_group}</span>}
        {hit.media_type && <span>{hit.media_type}</span>}
        {hit.language && <span>{hit.language}</span>}
        {hit.page_count ? <span>{hit.page_count} pages</span> : null}
        {hit.size ? <span>{(hit.size / 1024).toFixed(0)} KB</span> : null}
      </p>
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
  return out;
}

import { useSearch } from "@tanstack/react-router";
function useSearchParamsSafe(): SearchParams {
  return useSearch({ from: "/" });
}

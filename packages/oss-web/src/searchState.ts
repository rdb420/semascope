// The search page's state in the URL, and the API payload it stands for (docs/search.md).
import type { FacetValue, SearchIn } from "./api";

export type Filters = Record<string, string[]>;

export type SearchParams = {
  q?: string;
  collection?: string;
  page?: number;
  f?: Filters; // a field's values: any of them
  x?: Filters; // a field's excluded values: none of them
  view?: "list" | "media" | "terms";
  mode?: "semantic" | "hybrid";
  sort?: string; // a SORTS key (the table view's links may carry an API sort, read as its key)
  op?: "all" | "any" | "phrase";
  stem?: false; // other word forms off ("exact words")
  from?: string; // a date range on modified_dt, [from, to)
  to?: string;
};

export const SORTS = {
  relevance: { label: "Relevance", api: null },
  newest: { label: "Newest first", api: "modified_dt desc,indexed_dt desc" },
  oldest: { label: "Oldest first", api: "modified_dt asc" },
  title: { label: "Title A–Z", api: "title_s asc" },
  largest: { label: "Largest first", api: "size_l desc" },
} as const;
export type SortKey = keyof typeof SORTS;

/** A sort parameter as a SORTS key: a key, or the API sort one stands for; else relevance. */
export function sortKey(v: unknown): SortKey {
  if (typeof v !== "string") return "relevance";
  if (v in SORTS) return v as SortKey;
  return (Object.keys(SORTS) as SortKey[]).find((k) => SORTS[k].api === v) ?? "relevance";
}

export const OPERATORS = {
  "": "Most words",
  all: "All words",
  any: "Any word",
  phrase: "Exact phrase",
} as const;

export const LIST_PAGE_SIZE = 10;
export const MEDIA_PAGE_SIZE = 40;
export const DATE_FIELD = "modified_dt";

/** A URL value as text. The router parses `?q=1984` into the number 1984, which must stay a query. */
function text(v: unknown): string | undefined {
  if (typeof v === "string") return v || undefined;
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  if (typeof v === "boolean") return String(v);
  return undefined;
}

function filters(v: unknown): Filters | undefined {
  if (!v || typeof v !== "object" || Array.isArray(v)) return undefined;
  const out: Filters = {};
  for (const [k, vs] of Object.entries(v as Record<string, unknown>)) {
    const values = (Array.isArray(vs) ? vs : [vs]).map(text).filter((s): s is string => !!s);
    if (values.length) out[k] = values;
  }
  return Object.keys(out).length ? out : undefined;
}

const DAY = /^\d{4}(-\d{2}(-\d{2})?)?$/;

/** The page's search parameters from the URL; anything it does not understand is dropped. */
export function searchSearchSchema(raw: Record<string, unknown>): SearchParams {
  const out: SearchParams = {};
  const q = text(raw.q);
  if (q) out.q = q;
  const collection = text(raw.collection);
  if (collection) out.collection = collection;
  const page = typeof raw.page === "number" ? raw.page : Number(text(raw.page));
  if (Number.isInteger(page) && page > 1) out.page = page;
  const f = filters(raw.f);
  if (f) out.f = f;
  const x = filters(raw.x);
  if (x) out.x = x;
  if (raw.view === "media" || raw.view === "terms") out.view = raw.view;
  if (raw.mode === "semantic" || raw.mode === "hybrid") out.mode = raw.mode;
  const sort = sortKey(raw.sort);
  if (sort !== "relevance") out.sort = sort;
  if (raw.op === "all" || raw.op === "any" || raw.op === "phrase") out.op = raw.op;
  if (raw.stem === false || raw.stem === "false" || raw.stem === 0 || raw.stem === "0") out.stem = false;
  const from = text(raw.from);
  if (from && DAY.test(from)) out.from = from;
  const to = text(raw.to);
  if (to && DAY.test(to)) out.to = to;
  return out;
}

/** The parameters without empty or default values, for tidy URLs. */
export function clean(p: SearchParams): SearchParams {
  const out: SearchParams = {};
  if (p.q) out.q = p.q;
  if (p.collection) out.collection = p.collection;
  if (p.page && p.page > 1) out.page = p.page;
  if (p.f && Object.keys(p.f).length) out.f = p.f;
  if (p.x && Object.keys(p.x).length) out.x = p.x;
  if (p.view && p.view !== "list") out.view = p.view;
  if (p.mode) out.mode = p.mode;
  if (p.sort && sortKey(p.sort) !== "relevance") out.sort = sortKey(p.sort);
  if (p.op) out.op = p.op;
  if (p.stem === false) out.stem = false;
  if (p.from) out.from = p.from;
  if (p.to) out.to = p.to;
  return out;
}

/** The histogram's bar width for a date range: years, then months within about a year, then days. */
export function histogramGap(from?: string, to?: string): "+1YEAR" | "+1MONTH" | "+1DAY" {
  if (!from || !to) return "+1YEAR";
  const days = (Date.parse(to) - Date.parse(from)) / 86_400_000;
  if (!Number.isFinite(days) || days > 400) return "+1YEAR";
  if (days > 62) return "+1MONTH";
  return "+1DAY";
}

const iso = (d: Date) => d.toISOString().slice(0, 10);

/** The date range of one histogram bar (a bucket's start date and the bar width). */
export function barRange(bucket: string, gap: string): { from: string; to: string } {
  const start = new Date(`${bucket.slice(0, 10)}T00:00:00Z`);
  const end = new Date(start);
  if (gap === "+1YEAR") end.setUTCFullYear(end.getUTCFullYear() + 1);
  else if (gap === "+1MONTH") end.setUTCMonth(end.getUTCMonth() + 1);
  else end.setUTCDate(end.getUTCDate() + 1);
  return { from: iso(start), to: iso(end) };
}

/** How a date range reads: a year, a month, a day, or from–to. */
export function rangeLabel(from?: string, to?: string): string {
  if (from && to) {
    if (from.endsWith("-01-01") && barRange(from, "+1YEAR").to === to) return from.slice(0, 4);
    if (from.endsWith("-01") && barRange(from, "+1MONTH").to === to) return from.slice(0, 7);
    if (barRange(from, "+1DAY").to === to) return from;
  }
  return `${from ?? "…"} – ${to ?? "…"}`;
}

/** The label of a histogram bucket at a bar width. */
export function bucketLabel(bucket: string, gap: string): string {
  if (gap === "+1YEAR") return bucket.slice(0, 4);
  if (gap === "+1MONTH") return bucket.slice(0, 7);
  return bucket.slice(0, 10);
}

/** The API payload for the page's parameters. */
export function toPayload(p: SearchParams, facets: string[], more: string[] = []): SearchIn {
  const view = p.view ?? "list";
  const filters = p.f ?? {};
  return {
    q: p.q || "*:*",
    collection: p.collection ?? null,
    filters: view === "media" ? { ...filters, content_type_group_s: ["image"] } : filters,
    exclude: p.x ?? {},
    facets,
    facet_more: more,
    page: p.page ?? 1,
    page_size: view === "media" ? MEDIA_PAGE_SIZE : LIST_PAGE_SIZE,
    sort: SORTS[sortKey(p.sort)].api,
    histogram: DATE_FIELD,
    histogram_gap: histogramGap(p.from, p.to),
    dates: p.from || p.to ? { field: DATE_FIELD, start: p.from ?? null, end: p.to ?? null } : null,
    mode: p.mode ?? "keyword",
    stemming: p.stem !== false,
    operator: p.op ?? null,
  };
}

/** What a saved search stores: the payload plus the view, so restoring it shows the same page. */
export function toSaved(p: SearchParams, facets: string[]): SearchIn & { view?: string } {
  return { ...toPayload({ ...p, page: undefined }, facets), view: p.view ?? "list" };
}

/** The page parameters for a saved search's payload (saved before or after views were kept). */
export function fromSaved(query: SearchIn & { view?: unknown }): SearchParams {
  const view = query.view === "media" || query.view === "terms" ? query.view : undefined;
  const f = { ...(query.filters ?? {}) };
  if (view === "media" && f.content_type_group_s?.length === 1 && f.content_type_group_s[0] === "image") delete f.content_type_group_s;
  const sort = sortKey(query.sort);
  const p: SearchParams = {
    q: query.q && query.q !== "*:*" ? query.q : undefined,
    collection: query.collection ?? undefined,
    f,
    x: query.exclude,
    view,
    mode: query.mode === "semantic" || query.mode === "hybrid" ? query.mode : undefined,
    sort: sort === "relevance" ? undefined : sort,
    op: query.operator ?? undefined,
    stem: query.stemming === false ? false : undefined,
    from: query.dates?.start?.slice(0, 10) ?? undefined,
    to: query.dates?.end?.slice(0, 10) ?? undefined,
  };
  return clean(p);
}

/** Toggle one value in a filter map; empty fields are removed. */
export function toggleValue(map: Filters | undefined, field: string, value: string): Filters | undefined {
  const current = new Set(map?.[field] ?? []);
  if (current.has(value)) current.delete(value);
  else current.add(value);
  const next: Filters = { ...(map ?? {}), [field]: [...current] };
  if (next[field].length === 0) delete next[field];
  return Object.keys(next).length ? next : undefined;
}

// --- facets and suggestions ---------------------------------------------------------------

const FACET_LABELS: Record<string, string> = {
  persons_ss: "People",
  organisations_ss: "Organisations",
  places_ss: "Places",
  entity_labels_ss: "Linked entities",
  concept_labels_ss: "Concepts",
  content_type_group_s: "Type",
  language_s: "Language",
  media_type_s: "Format",
  source_id_s: "Source",
  emails_ss: "Email addresses",
  urls_ss: "Links",
  phones_ss: "Phone numbers",
  money_ss: "Amounts",
  hashtags_ss: "Hashtags",
};

/** Values shown before "show more". */
export const FACET_COLLAPSED = 8;

export function facetTitle(field: string): string {
  if (FACET_LABELS[field]) return FACET_LABELS[field];
  if (field.endsWith("_paths")) {
    const name = field.slice(0, -6).replace(/^x_/, "").replace(/[-_]/g, " ");
    return name.charAt(0).toUpperCase() + name.slice(1);
  }
  if (field.endsWith("_entities_ss")) {
    const cls = field.replace(/^x_/, "").slice(0, -"_entities_ss".length).replace(/_/g, " ");
    return cls.charAt(0).toUpperCase() + cls.slice(1) + " (found)";
  }
  return field;
}

export type TreeNode = { value: string; label: string; count: number; children: TreeNode[] };

/** A `*_paths` facet's values ("a", "a/b", "a/b/c") as a tree. */
export function buildTree(values: FacetValue[]): TreeNode[] {
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

/** The words being typed, as the suggester takes them: nothing for a field query or an operator. */
export function suggestTerm(value: string): string {
  const v = value.trim();
  if (v.length < 2 || /[:()"*?~^]/.test(v)) return "";
  return v;
}

/** A suggestion as a query: the title as one phrase. */
export function suggestionQuery(title: string): string {
  return `"${title.replace(/["\\]/g, " ").replace(/\s+/g, " ").trim()}"`;
}

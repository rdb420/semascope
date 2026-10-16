import type { SearchIn } from "./api";

/** The search state the table, entities and graph views share with the search page. */
export type ViewParams = {
  q?: string;
  collection?: string;
  f?: Record<string, string[]>;
  mode?: "semantic" | "hybrid";
  sort?: string;
  page?: number;
};

export function viewSearchSchema(raw: Record<string, unknown>): ViewParams {
  const out: ViewParams = {};
  if ((typeof raw.q === "string" || typeof raw.q === "number") && String(raw.q)) out.q = String(raw.q);
  if (typeof raw.collection === "string" && raw.collection) out.collection = raw.collection;
  if (raw.f && typeof raw.f === "object" && !Array.isArray(raw.f)) {
    const f: Record<string, string[]> = {};
    for (const [k, v] of Object.entries(raw.f as Record<string, unknown>)) {
      if (Array.isArray(v)) f[k] = v.map(String);
    }
    if (Object.keys(f).length) out.f = f;
  }
  if (raw.mode === "semantic" || raw.mode === "hybrid") out.mode = raw.mode;
  if (typeof raw.sort === "string" && raw.sort) out.sort = raw.sort;
  if (typeof raw.page === "number" && raw.page > 1) out.page = raw.page;
  return out;
}

/** The API payload for these parameters. */
export function toSearchIn(p: ViewParams, extra: Partial<SearchIn> = {}): SearchIn {
  return {
    q: p.q || "*:*",
    collection: p.collection ?? null,
    filters: p.f ?? {},
    mode: p.mode ?? "keyword",
    ...extra,
  };
}

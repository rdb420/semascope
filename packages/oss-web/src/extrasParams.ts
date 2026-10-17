// URL parameters of the ADR-0012 pages (analysis, diff, synonyms, explain, export).
import type { SearchIn } from "./api";
import { toSearchIn, viewSearchSchema, type ViewParams } from "./viewParams";

const str = (v: unknown): string | undefined => (typeof v === "string" && v ? v : typeof v === "number" ? String(v) : undefined);

export type AnalysisTab = "clusters" | "compare" | "tree";
export type AnalysisParams = ViewParams & { tab?: AnalysisTab; word?: string; side?: "left"; vs?: string; k?: number };

export function analysisSearchSchema(raw: Record<string, unknown>): AnalysisParams {
  const out: AnalysisParams = viewSearchSchema(raw);
  if (raw.tab === "compare" || raw.tab === "tree") out.tab = raw.tab;
  const word = str(raw.word);
  if (word) out.word = word;
  if (raw.side === "left") out.side = "left";
  const vs = str(raw.vs);
  if (vs) out.vs = vs;
  const k = Number(raw.k);
  if (Number.isInteger(k) && k >= 1 && k <= 20) out.k = k;
  return out;
}

export type DiffParams = { a: string; b: string };

export function diffSearchSchema(raw: Record<string, unknown>): DiffParams {
  return { a: str(raw.a) ?? "", b: str(raw.b) ?? "" };
}

export type SynonymParams = { q?: string; iri?: string; collection?: string };

export function synonymsSearchSchema(raw: Record<string, unknown>): SynonymParams {
  const out: SynonymParams = {};
  for (const key of ["q", "iri", "collection"] as const) {
    const v = str(raw[key]);
    if (v) out[key] = v;
  }
  return out;
}

export type ExplainParams = ViewParams & { doc: string; op?: "all" | "any" | "phrase"; stem?: false };

export function explainSearchSchema(raw: Record<string, unknown>): ExplainParams {
  const out: ExplainParams = { ...viewSearchSchema(raw), doc: str(raw.doc) ?? "" };
  if (raw.op === "all" || raw.op === "any" || raw.op === "phrase") out.op = raw.op;
  if (raw.stem === false || raw.stem === "false") out.stem = false;
  return out;
}

/** The search an explanation is about, as the search page sends it. */
export function explainSearch(p: ExplainParams): SearchIn {
  return toSearchIn(p, { operator: p.op ?? null, stemming: p.stem !== false, facets: [], page_size: 10 });
}

// API calls of the ADR-0012 pages (analysis, diff, exports, synonyms, explain). Only lazily
// loaded pages import this module, so none of it reaches the search page's first load.
import { ApiError, apiRequest as request, ensureCsrf, type SearchIn } from "./api";

const enc = encodeURIComponent;
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export type TermWeight = { term: string; weight: number };
export type Cluster = {
  id: number;
  size: number;
  coherence: number;
  terms: TermWeight[];
  documents: { id: string; title: string; collection: string; similarity: number }[];
};
export type ClustersOut = { documents: number; k: number; features: string; clusters: Cluster[]; vector_error: string | null };
export type CompareTerm = { term: string; a_docs: number; b_docs: number; a_share: number; b_share: number; g2: number };
export type CompareOut = { a_documents: number; b_documents: number; background: "search" | "index"; more: CompareTerm[]; less: CompareTerm[] };
export type TreeNode = { word: string; count: number; documents: number; example_documents: string[]; children: TreeNode[]; more: number };
export type WordTreeOut = {
  phrase: string;
  side: "left" | "right";
  sampled: number;
  occurrences: number;
  documents: number;
  truncated: boolean;
  root: TreeNode;
  titles: Record<string, string>;
};
export type DiffPiece = { op: "equal" | "delete" | "insert"; text: string };
export type DiffHunk = {
  op: "equal" | "skip" | "insert" | "delete" | "replace";
  a_start: number;
  b_start: number;
  lines?: string[];
  count?: number;
  a?: string[];
  b?: string[];
  words?: DiffPiece[];
};
export type DiffDoc = { id: string; title: string; collection: string };
export type DiffOut = {
  a: DiffDoc;
  b: DiffDoc;
  ratio: number;
  a_lines: number;
  b_lines: number;
  added: number;
  removed: number;
  changed: number;
  truncated: boolean;
  hunks: DiffHunk[];
};
export type ExpandMatch = {
  phrase: string;
  start: number;
  end: number;
  iri: string;
  pref_label: string;
  kind: string;
  pack_id: string;
  matched_label: string;
  matched_type: string;
  expands_to: { label: string; type: string; weight: number }[];
};
export type ExpandOut = {
  q: string;
  words: string[];
  matches: ExpandMatch[];
  drafts: { iri: string; label: string; label_type: string; collection: string; pack_id: string }[];
  tokens: { text: string; position: number; type: string }[];
  analysis_error: string | null;
};
export type Candidate = { label: string; reasons: string[]; documents: number; distance: number | null; lift: number | null; field: string | null };
export type SuggestOut = { iri: string; pref_label: string; pack_id: string; kind: string; labels: string[]; documents: number; candidates: Candidate[] };
export type ExplainNode = { value?: number; description?: string; match?: boolean; details?: ExplainNode[] };
export type ExplainOut = {
  doc_id: string;
  title: string;
  collection: string;
  mode: string;
  query: string;
  keyword: { rank: number | null; score: number | null; matched: boolean; clauses: { clause: string; weight: number }[]; explanation: ExplainNode | null } | null;
  semantic: { rank: number | null; candidates: number; passages: { text: string; page: number | null; similarity: number; chunk_rank: number | null }[] } | null;
  fused: { keyword_share: number; semantic_share: number; score: number; rank: number | null } | null;
  semantic_error: string | null;
  full: boolean;
};
export type ImportOut = {
  received: number;
  imported: number;
  already_imported: number;
  unmatched: number;
  anchored: number;
  unanchored: number;
  dry_run: boolean;
  problems: { id: string; reason: string }[];
};

export const analysis = {
  clusters: (search: SearchIn, k: number | null, features: "terms" | "vectors", sample = 200) =>
    request<ClustersOut>("/api/analytics/clusters", post({ search, k, features, sample })),
  compare: (search: SearchIn, against: SearchIn | null, top = 30) =>
    request<CompareOut>("/api/analytics/compare", post({ search, against, top })),
  wordTree: (search: SearchIn, phrase: string, side: "left" | "right", depth = 4, branches = 8) =>
    request<WordTreeOut>("/api/analytics/wordtree", post({ search, phrase, side, depth, branches })),
  diff: (a: string, b: string, context = 3) => request<DiffOut>(`/api/analytics/diff?a=${enc(a)}&b=${enc(b)}&context=${context}`),
  explain: (search: SearchIn, docId: string) => request<ExplainOut>("/api/explain", post({ search, doc_id: docId })),
  expand: (q: string, collection?: string) =>
    request<ExpandOut>(`/api/synonyms/expand?q=${enc(q)}${collection ? `&collection=${enc(collection)}` : ""}`),
  suggest: (iri: string, collection?: string) =>
    request<SuggestOut>(`/api/synonyms/suggest?iri=${enc(iri)}${collection ? `&collection=${enc(collection)}` : ""}`),
};

async function failure(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (body && body.detail) detail = String(body.detail);
  } catch {
    // not JSON: keep the status text
  }
  return new ApiError(res.status, detail);
}

/** Fetch an export and hand it to the browser as a file named `filename`. */
export async function download(path: string, filename: string, body?: unknown): Promise<void> {
  const init: RequestInit = { credentials: "same-origin" };
  if (body !== undefined) {
    init.method = "POST";
    init.headers = { "Content-Type": "application/json", "X-CSRFToken": await ensureCsrf() };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  if (!res.ok) throw await failure(res);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** W3C Web Annotations or a Hypothesis export from a file, into one collection. */
export async function importAnnotations(collection: string, file: File, dryRun: boolean): Promise<ImportOut> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`/api/annotations/w3c/import?collection=${enc(collection)}&dry_run=${dryRun}`, {
    method: "POST",
    body: form,
    headers: { "X-CSRFToken": await ensureCsrf(), Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!res.ok) throw await failure(res);
  return (await res.json()) as ImportOut;
}

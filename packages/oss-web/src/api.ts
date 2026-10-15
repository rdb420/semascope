// Typed client for the Semascope API. Session auth with CSRF for the browser.

export type FacetValue = { value: string; count: number };
export type SearchHit = {
  id: string;
  score: number | null;
  title: string;
  uri: string;
  collection: string;
  source_id: string;
  media_type: string | null;
  content_type_group: string | null;
  language: string | null;
  size: number | null;
  page_count: number | null;
  modified_at: string | null;
  indexed_at: string | null;
  preview_pages: number;
  snippets: string[];
};
export type SearchOut = {
  total: number;
  page: number;
  page_size: number;
  took_ms: number;
  hits: SearchHit[];
  facets: Record<string, FacetValue[]>;
  histogram: FacetValue[];
};
export type SearchIn = {
  q: string;
  collection?: string | null;
  filters?: Record<string, string[]>;
  facets?: string[];
  page?: number;
  page_size?: number;
  sort?: string | null;
  histogram?: string | null;
  histogram_gap?: string;
};
export type Place = { iri: string; label: string; type: string; lat: number; lon: number; docs: number };
export type SavedSearch = { id: number; name: string; query: SearchIn; alert: boolean; last_total: number };
export type Collection = {
  slug: string;
  name: string;
  description: string;
  profile: string;
  document_count: number;
};
export type Document = {
  id: string;
  collection: string;
  source_id: number;
  uri: string;
  title: string;
  media_type: string;
  language: string;
  size: number | null;
  status: string;
  error: string;
  modified_at: string | null;
  indexed_at: string | null;
  metadata: Record<string, unknown>;
  fields: Record<string, unknown>;
  stage_runs: { stage: string; outcome: string; duration_ms: number; message?: string | null }[];
};
export type DocumentText = { id: string; text: string; pages: string[] };
export type User = { username: string; is_staff: boolean; is_superuser: boolean };
export type Stats = {
  collections: number;
  sources: number;
  documents: Record<string, number>;
  solr_documents: number | null;
};

export type Pack = {
  id: number;
  pack_id: string;
  version: string;
  title: string;
  base_iri: string;
  status: string;
  bundle_status: string;
  licence: string;
  counts: Record<string, number>;
  findings: { severity: string; code: string; message: string }[];
  cq_results: { tests?: number; passed?: number; results?: { cq_id: string; status: string }[] };
  imported_at: string;
  activated_at: string | null;
};
export type PackDetail = Pack & {
  requirements: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  decisions: { id: string; topic: string; decision: string; rationale: string }[];
  sources: { id: string; title: string; location?: string }[];
  schemes: { slug: string; iri: string; label: string; definition: string }[];
};
export type TreeNode = { iri: string; label: string; path: string; kind: string; children: TreeNode[] };
export type Concept = {
  iri: string;
  kind: string;
  pref_label: string;
  lang: string;
  alt_labels: string[];
  hidden_labels: string[];
  definition: string;
  scheme: string;
  scheme_slug: string;
  type_iri: string;
  type_label: string;
  broader: string[];
  path: string;
  depth: number;
  taggable: boolean;
  support: string;
  term_status: string;
  pack_id: string;
  pack_version: string;
};

export type GraphNode = { id: string; label: string; kind: string; properties: Record<string, unknown> };
export type GraphEdge = { source: string; target: string; type: string; properties: Record<string, unknown> };
export type GraphOut = { nodes: GraphNode[]; edges: GraphEdge[] };
export type Hub = { label: string; key: string; labels: string[]; docs: number };

export type ReviewItem = {
  id: number;
  collection: string;
  document_id: string | null;
  document_title: string;
  kind: string;
  target: string;
  label: string;
  payload: Record<string, unknown>;
  confidence: number | null;
  status: string;
  note: string;
  created_at: string;
};
export type DocTag = {
  iri: string;
  label: string;
  kind: string;
  stage: string;
  confidence: number;
  pack_id?: string | null;
  span_count: number;
  curated: { decision: string } | null;
};
export type Annotation = { id: number; document_id: string; author: string | null; text: string; tags: string[]; span: Record<string, number> | null; created_at: string };

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function cookie(name: string): string | undefined {
  return document.cookie
    .split("; ")
    .find((row) => row.startsWith(name + "="))
    ?.split("=")[1];
}

async function ensureCsrf(): Promise<string> {
  const existing = cookie("csrftoken");
  if (existing) return existing;
  const res = await fetch("/api/auth/csrf", { credentials: "same-origin" });
  const body = (await res.json()) as { csrftoken: string };
  return body.csrftoken;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.method && init.method !== "GET") {
    headers.set("Content-Type", "application/json");
    headers.set("X-CSRFToken", await ensureCsrf());
  }
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const body = text ? (JSON.parse(text) as unknown) : null;
  if (!res.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : res.statusText;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

export const api = {
  me: () => request<User>("/api/auth/me"),
  login: (username: string, password: string) =>
    request<User>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  collections: () => request<Collection[]>("/api/collections"),
  search: (payload: SearchIn) =>
    request<SearchOut>("/api/search", { method: "POST", body: JSON.stringify(payload) }),
  document: (id: string) => request<Document>(`/api/documents/${id}`),
  documentText: (id: string) => request<DocumentText>(`/api/documents/${id}/text`),
  stats: () => request<Stats>("/api/stats"),
  graphNeighbours: (id: string, depth = 1) =>
    request<GraphOut>(`/api/graph/neighbours?id=${encodeURIComponent(id)}&depth=${depth}`),
  graphPath: (a: string, b: string) =>
    request<GraphOut>(`/api/graph/path?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  listSearch: (names: string[], collection?: string) =>
    request<Record<string, { total: number; hits: SearchHit[] }>>("/api/search/list", {
      method: "POST",
      body: JSON.stringify({ names, collection: collection ?? null }),
    }),
  graphShared: (collection?: string, kind = "Entity") =>
    request<Hub[]>(`/api/graph/shared?kind=${kind}${collection ? `&collection=${encodeURIComponent(collection)}` : ""}`),
  reviews: (q: { collection?: string; kind?: string }) =>
    request<ReviewItem[]>(
      `/api/curation/reviews?status=open${q.collection ? `&collection=${encodeURIComponent(q.collection)}` : ""}${q.kind ? `&kind=${q.kind}` : ""}`,
    ),
  reviewSummary: (collection?: string) =>
    request<Record<string, number>>(`/api/curation/reviews/summary${collection ? `?collection=${encodeURIComponent(collection)}` : ""}`),
  decideReview: (id: number, body: { decision: string; note: string; link_to?: string; label?: string }) =>
    request<ReviewItem>(`/api/curation/reviews/${id}/decision`, { method: "POST", body: JSON.stringify(body) }),
  documentTags: (id: string) => request<DocTag[]>(`/api/curation/documents/${id}/tags`),
  decideTag: (id: string, body: { iri: string; decision: string; label?: string; kind?: string; reason?: string }) =>
    request<unknown>(`/api/curation/documents/${id}/tags`, { method: "POST", body: JSON.stringify(body) }),
  annotations: (id: string) => request<Annotation[]>(`/api/curation/documents/${id}/annotations`),
  addAnnotation: (id: string, body: { text: string; tags?: string[] }) =>
    request<Annotation>(`/api/curation/documents/${id}/annotations`, { method: "POST", body: JSON.stringify(body) }),
  places: (collection?: string) =>
    request<Place[]>(`/api/knowledge/places${collection ? `?collection=${encodeURIComponent(collection)}` : ""}`),
  savedSearches: () => request<SavedSearch[]>("/api/search/saved"),
  saveSearch: (body: { name: string; query: SearchIn; alert: boolean }) =>
    request<SavedSearch>("/api/search/saved", { method: "POST", body: JSON.stringify(body) }),
  deleteSavedSearch: (id: number) => request<void>(`/api/search/saved/${id}`, { method: "DELETE" }),
  terms: (payload: SearchIn) =>
    request<{ term: string; count: number; docs: number }[]>("/api/search/terms", { method: "POST", body: JSON.stringify(payload) }),
  packs: () => request<Pack[]>("/api/knowledge/packs"),
  pack: (packId: string) => request<PackDetail>(`/api/knowledge/packs/${packId}`),
  packTree: (packId: string, scheme?: string) =>
    request<TreeNode[]>(`/api/knowledge/packs/${packId}/tree${scheme ? `?scheme=${encodeURIComponent(scheme)}` : ""}`),
  concept: (iri: string) => request<Concept>(`/api/knowledge/concepts?iri=${encodeURIComponent(iri)}`),
  tagQuery: (q: string) =>
    request<{ phrase: string; id: string; name: string; type: { id: string; name: string }[] }[]>(
      "/api/search/tag-query",
      { method: "POST", body: JSON.stringify({ q }) },
    ),
  proposeDelta: (packId: string, payload: { collection: string; kind: string; params: Record<string, unknown>; reason?: string }) =>
    request<{ id: number; status: string }>(`/api/knowledge/packs/${packId}/deltas`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deltas: (packId: string) =>
    request<{ id: number; kind: string; params: Record<string, unknown>; reason: string; status: string; collection: string; created_at: string }[]>(
      `/api/knowledge/packs/${packId}/deltas`,
    ),
  conceptDocuments: (iri: string) =>
    request<{ total: number; docs: { id: string; title_txt?: string; uri_s: string }[] }>(
      `/api/knowledge/concepts/documents?iri=${encodeURIComponent(iri)}`,
    ),
};

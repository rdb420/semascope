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
  snippets: string[];
};
export type SearchOut = {
  total: number;
  page: number;
  page_size: number;
  took_ms: number;
  hits: SearchHit[];
  facets: Record<string, FacetValue[]>;
};
export type SearchIn = {
  q: string;
  collection?: string | null;
  filters?: Record<string, string[]>;
  facets?: string[];
  page?: number;
  page_size?: number;
  sort?: string | null;
};
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
};

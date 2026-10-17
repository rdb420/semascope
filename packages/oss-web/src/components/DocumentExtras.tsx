import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

const PANEL = "rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900";
const MODES = [
  { value: "hybrid", label: "Both" },
  { value: "text", label: "Words" },
  { value: "vector", label: "Meaning" },
] as const;

/** Documents like this one: MoreLikeThis on the words, nearest vectors on the meaning, or both. */
export function SimilarPanel({ docId }: { docId: string }) {
  const [mode, setMode] = useState<(typeof MODES)[number]["value"]>("hybrid");
  const similar = useQuery({ queryKey: ["similar", docId, mode], queryFn: () => api.similar(docId, mode) });
  const note = similar.data?.vector_error && mode !== "text" ? "Meaning-based matches are unavailable here." : "";
  return (
    <div className={PANEL} data-testid="similar-panel">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase text-zinc-500">Similar documents</h2>
        <div className="flex gap-1" role="group" aria-label="Similar by">
          {MODES.map((m) => (
            <button
              key={m.value}
              onClick={() => setMode(m.value)}
              aria-pressed={mode === m.value}
              className={`rounded px-1.5 text-xs ${mode === m.value ? "bg-blue-600 text-white" : "bg-zinc-100 hover:bg-zinc-200 dark:bg-zinc-800"}`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      {similar.isLoading && <p className="text-xs text-zinc-500">Looking…</p>}
      {similar.isError && <p className="text-xs text-red-600">{String(similar.error)}</p>}
      {note && <p className="mb-1 text-xs text-zinc-500">{note}</p>}
      {similar.data && similar.data.hits.length === 0 && <p className="text-xs text-zinc-500">Nothing similar found.</p>}
      <ul className="space-y-1">
        {similar.data?.hits.map((h) => (
          <li key={h.id} className="flex items-baseline gap-2">
            <Link to="/documents/$id" params={{ id: h.id }} className="truncate text-blue-700 hover:underline dark:text-blue-400" title={h.uri}>
              {h.title || h.uri}
            </Link>
            <span className="ml-auto shrink-0 text-xs text-zinc-400" title="rank by words / by meaning">
              {h.text_rank ?? "–"}/{h.vector_rank ?? "–"}
            </span>
            <Link to="/diff" search={{ a: docId, b: h.id }} className="shrink-0 text-xs text-blue-700 hover:underline dark:text-blue-400" title="compare the two texts">
              diff
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Downloads of this document as RDF and of its notes and decisions as Web Annotations. */
export function ExportLinks({ docId }: { docId: string }) {
  const id = encodeURIComponent(docId);
  const links = [
    { href: `/api/export/documents/${id}?format=turtle`, label: "RDF (Turtle)" },
    { href: `/api/export/documents/${id}?format=jsonld`, label: "RDF (JSON-LD)" },
    { href: `/api/export/documents/${id}?format=nquads`, label: "RDF (N-Quads)" },
    { href: `/api/annotations/w3c?document=${id}`, label: "Web Annotations" },
  ];
  return (
    <div className={PANEL}>
      <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Export</h2>
      <ul className="flex flex-wrap gap-x-3 gap-y-1">
        {links.map((l) => (
          <li key={l.href}>
            <a href={l.href} className="text-blue-700 hover:underline dark:text-blue-400" download>
              {l.label}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function DocumentExtras({ docId }: { docId: string }) {
  return (
    <>
      <SimilarPanel docId={docId} />
      <ExportLinks docId={docId} />
    </>
  );
}

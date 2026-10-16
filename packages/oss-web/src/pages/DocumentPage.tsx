import { useParams, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, type TagSpan } from "../api";
import { useUser } from "../components/Shell";
import { Highlighted, TagExplain } from "../components/TagExplain";
import { DocumentExtras } from "../components/DocumentExtras";

const PAGE_SEPARATOR = "\n\n"; // the structure stage joins pages with a blank line

/** Where each page starts in the document text, or null when the pages do not make up the text. */
function pageStarts(text: string, pages: string[]): number[] | null {
  if (!pages.length || pages.join(PAGE_SEPARATOR) !== text) return null;
  const starts: number[] = [];
  let pos = 0;
  for (const p of pages) {
    starts.push(pos);
    pos += p.length + PAGE_SEPARATOR.length;
  }
  return starts;
}

export function DocumentPage() {
  const { id } = useParams({ from: "/documents/$id" });
  const user = useUser();
  const doc = useQuery({ queryKey: ["document", id], queryFn: () => api.document(id), enabled: user.isSuccess });
  const text = useQuery({ queryKey: ["document-text", id], queryFn: () => api.documentText(id), enabled: user.isSuccess });
  const tags = useQuery({ queryKey: ["document-tags", id], queryFn: () => api.documentTags(id), enabled: user.isSuccess });
  const notes = useQuery({ queryKey: ["document-notes", id], queryFn: () => api.annotations(id), enabled: user.isSuccess });
  const qc = useQueryClient();
  const decideTag = useMutation({
    mutationFn: (body: { iri: string; decision: string; label?: string; kind?: string }) => api.decideTag(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document-tags", id] });
      qc.invalidateQueries({ queryKey: ["tag-history", id] });
    },
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [occurrence, setOccurrence] = useState(0);
  const tag = useMemo(() => {
    const found = selected ? tags.data?.find((t) => t.iri === selected) : undefined;
    // occurrences in reading order (spans merged by the link stage come after the dictionary's)
    return found ? { ...found, spans: [...found.spans].sort((a, b) => a.start - b.start) } : undefined;
  }, [selected, tags.data]);
  const spans: TagSpan[] = tag?.spans ?? [];
  function select(iri: string) {
    setSelected(iri === selected ? null : iri);
    setOccurrence(0);
  }
  useEffect(() => {
    if (tag) document.getElementById("tag-occurrence")?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [tag, occurrence]);
  const [note, setNote] = useState("");
  const addNote = useMutation({
    mutationFn: () => api.addAnnotation(id, { text: note }),
    onSuccess: () => {
      setNote("");
      qc.invalidateQueries({ queryKey: ["document-notes", id] });
    },
  });
  function submitNote(e: FormEvent) {
    e.preventDefault();
    if (note.trim()) addNote.mutate();
  }

  if (!user.isSuccess) return null;
  if (doc.isError) return <p className="text-red-600">{String(doc.error)}</p>;
  if (!doc.data) return <p className="text-zinc-500">Loading…</p>;
  const d = doc.data;
  const previewPages = Number(d.fields.preview_pages ?? 0);
  const entities = Object.entries(d.fields).filter(([k, v]) => Array.isArray(v) && (v as unknown[]).length && !k.endsWith("_path") && !["chunk_pages", "preview_pages", "page_starts"].includes(k));
  const starts = text.data ? pageStarts(text.data.text, text.data.pages) : null;

  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_18rem]">
      <article className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
        <Link to="/" className="text-sm text-blue-700 hover:underline dark:text-blue-400">
          ← Back to search
        </Link>
        <h1 className="mt-2 text-xl font-semibold">{d.title || d.uri}</h1>
        <Link to="/graph" search={{ id: `Document:${d.id}`, collection: d.collection }} className="text-sm text-blue-700 hover:underline dark:text-blue-400">
          Show in graph
        </Link>
        <p className="truncate text-xs text-zinc-500">{d.uri}</p>
        {previewPages > 0 && (
          <div className="mt-4 flex gap-2 overflow-x-auto pb-2">
            {Array.from({ length: previewPages }, (_, i) => (
              <a key={i} href={`/api/documents/${d.id}/preview/${i + 1}`} target="_blank" rel="noreferrer" className="shrink-0">
                <img
                  src={`/api/documents/${d.id}/preview/${i + 1}`}
                  alt={`Page ${i + 1}`}
                  loading="lazy"
                  className="h-40 rounded border border-zinc-200 dark:border-zinc-700"
                />
              </a>
            ))}
          </div>
        )}
        {tag && (
          <p className="mt-4 rounded bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-200">
            Showing {spans.length} occurrence{spans.length === 1 ? "" : "s"} of <strong>{tag.label}</strong>.{" "}
            <button onClick={() => setSelected(null)} className="underline">
              Clear
            </button>
          </p>
        )}
        {text.data?.pages.length && starts ? (
          <div className="mt-4 space-y-6">
            {text.data.pages.map((p, i) => (
              <section key={i}>
                {text.data!.pages.length > 1 && <h2 className="mb-1 text-xs font-semibold uppercase text-zinc-500">Page {i + 1}</h2>}
                <pre className="whitespace-pre-wrap font-sans text-sm leading-6">
                  <Highlighted text={p} base={starts[i]} spans={spans} active={occurrence} />
                </pre>
              </section>
            ))}
          </div>
        ) : (
          <pre className="mt-4 whitespace-pre-wrap font-sans text-sm leading-6">
            <Highlighted text={text.data?.text ?? ""} base={0} spans={spans} active={occurrence} />
          </pre>
        )}
      </article>
      <aside className="space-y-4 text-sm">
        <dl className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <Row k="Collection" v={d.collection} />
          <Row k="Status" v={d.status} />
          <Row k="Format" v={d.media_type} />
          <Row k="Language" v={d.language} />
          <Row k="Size" v={d.size ? `${(d.size / 1024).toFixed(0)} KB` : ""} />
          <Row k="Pages" v={String(d.metadata.page_count ?? "")} />
          <Row k="OCR" v={d.metadata.ocr ? "yes" : "no"} />
          <Row k="Modified" v={d.modified_at ?? ""} />
          <Row k="Indexed" v={d.indexed_at ?? ""} />
        </dl>
        {entities.length > 0 && (
          <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Found in this document</h2>
            {entities.map(([k, v]) => (
              <div key={k} className="mb-2">
                <div className="text-xs text-zinc-500">{k}</div>
                <div className="flex flex-wrap gap-1">
                  {(v as string[]).slice(0, 30).map((x) => (
                    <span key={x} className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs dark:bg-zinc-800">
                      {x}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        {tag && <TagExplain docId={id} tag={tag} occurrence={occurrence} onOccurrence={setOccurrence} onClose={() => setSelected(null)} />}
        {tags.data && tags.data.length > 0 && (
          <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Tags ({tags.data.length})</h2>
            <p className="mb-2 text-xs text-zinc-500">Select a tag to see where it occurs and why.</p>
            <ul className="max-h-96 space-y-1 overflow-y-auto">
              {[...tags.data].sort((a, b) => b.span_count - a.span_count).map((t) => (
                <li key={t.iri} className={`flex items-center gap-2 rounded px-1 ${t.iri === selected ? "bg-amber-50 dark:bg-amber-950" : ""}`}>
                  <button
                    onClick={() => select(t.iri)}
                    aria-pressed={t.iri === selected}
                    className={`truncate text-left hover:underline ${t.rejected ? "line-through text-zinc-400" : ""}`}
                    title={`${t.stage}${t.stage_version ? ` v${t.stage_version}` : ""} · ${t.confidence.toFixed(2)}${t.pack_id ? ` · ${t.pack_id}@${t.pack_version ?? "?"}` : ""}`}
                  >
                    {t.label}
                  </button>
                  <span className="text-xs text-zinc-400">{t.span_count}</span>
                  <span className="ml-auto flex gap-1">
                    <button title="accept" onClick={() => decideTag.mutate({ iri: t.iri, decision: "accept", label: t.label, kind: t.kind })} className={`rounded px-1 text-xs ${t.curated?.decision === "accept" ? "bg-green-600 text-white" : "bg-zinc-100 hover:bg-green-100 dark:bg-zinc-800"}`}>✓</button>
                    <button title="reject" onClick={() => decideTag.mutate({ iri: t.iri, decision: "reject", label: t.label, kind: t.kind })} className={`rounded px-1 text-xs ${t.curated?.decision === "reject" ? "bg-red-600 text-white" : "bg-zinc-100 hover:bg-red-100 dark:bg-zinc-800"}`}>✕</button>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Notes</h2>
          <ul className="mb-2 space-y-1">
            {notes.data?.map((n) => (
              <li key={n.id}>
                <span className="text-zinc-500">{n.author ?? "?"}:</span> {n.text}
              </li>
            ))}
          </ul>
          <form onSubmit={submitNote} className="flex gap-1">
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note" className="min-w-0 flex-1 rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700" />
            <button disabled={addNote.isPending || !note.trim()} className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-40">Add</button>
          </form>
        </div>
        <DocumentExtras docId={id} />
        <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Pipeline</h2>
          <ul className="space-y-1">
            {d.stage_runs.map((r, i) => (
              <li key={i} className="flex justify-between">
                <span>
                  {r.stage} <span className={r.outcome === "ok" ? "text-green-600" : r.outcome === "failed" ? "text-red-600" : "text-zinc-500"}>{r.outcome}</span>
                </span>
                <span className="text-zinc-500">{r.duration_ms} ms</span>
              </li>
            ))}
          </ul>
          {d.error && <p className="mt-2 text-red-600">{d.error}</p>}
        </div>
      </aside>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  if (!v) return null;
  return (
    <div className="flex justify-between gap-2 py-0.5">
      <dt className="text-zinc-500">{k}</dt>
      <dd className="truncate text-right">{v}</dd>
    </div>
  );
}

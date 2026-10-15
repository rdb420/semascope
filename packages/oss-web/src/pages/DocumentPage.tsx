import { useParams, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useUser } from "../components/Shell";

export function DocumentPage() {
  const { id } = useParams({ from: "/documents/$id" });
  const user = useUser();
  const doc = useQuery({ queryKey: ["document", id], queryFn: () => api.document(id), enabled: user.isSuccess });
  const text = useQuery({ queryKey: ["document-text", id], queryFn: () => api.documentText(id), enabled: user.isSuccess });

  if (!user.isSuccess) return null;
  if (doc.isError) return <p className="text-red-600">{String(doc.error)}</p>;
  if (!doc.data) return <p className="text-zinc-500">Loading…</p>;
  const d = doc.data;
  const entities = Object.entries(d.fields).filter(([k, v]) => Array.isArray(v) && (v as unknown[]).length && !k.endsWith("_path") && k !== "chunk_pages");

  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_18rem]">
      <article className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
        <Link to="/" className="text-sm text-blue-700 hover:underline dark:text-blue-400">
          ← Back to search
        </Link>
        <h1 className="mt-2 text-xl font-semibold">{d.title || d.uri}</h1>
        <p className="truncate text-xs text-zinc-500">{d.uri}</p>
        {text.data?.pages.length ? (
          <div className="mt-4 space-y-6">
            {text.data.pages.map((p, i) => (
              <section key={i}>
                {text.data!.pages.length > 1 && <h2 className="mb-1 text-xs font-semibold uppercase text-zinc-500">Page {i + 1}</h2>}
                <pre className="whitespace-pre-wrap font-sans text-sm leading-6">{p}</pre>
              </section>
            ))}
          </div>
        ) : (
          <pre className="mt-4 whitespace-pre-wrap font-sans text-sm leading-6">{text.data?.text}</pre>
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

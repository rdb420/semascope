import { useState, type FormEvent } from "react";
import { useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { download, importAnnotations, type ImportOut } from "../extrasApi";
import { useUser } from "../components/Shell";
import { SearchSummary, ViewLinks } from "../components/ViewLinks";
import { toSearchIn, type ViewParams } from "../viewParams";

const PANEL = "rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900";
const INPUT = "rounded border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700";
const BUTTON = "rounded border px-2 py-0.5 text-sm hover:bg-zinc-100 disabled:opacity-40 dark:hover:bg-zinc-800";
const TABLES = [
  { format: "csv", label: "CSV" },
  { format: "jsonl", label: "JSON Lines" },
  { format: "parquet", label: "Parquet" },
] as const;
const RDF = [
  { format: "turtle", label: "Turtle", ext: "ttl" },
  { format: "jsonld", label: "JSON-LD", ext: "jsonld" },
  { format: "nquads", label: "N-Quads", ext: "nq" },
] as const;

/** Exports of a search (tables and RDF), a collection's graph, and the annotation import. */
export function ExportPage() {
  const params = useSearch({ from: "/export" }) as ViewParams;
  const user = useUser();
  const [limit, setLimit] = useState(1000);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const [graphCollection, setGraphCollection] = useState(params.collection ?? "");

  async function run(key: string, path: string, filename: string, body?: unknown) {
    setBusy(key);
    setError(null);
    try {
      await download(path, filename, body);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  if (!user.isSuccess) return null;
  const search = toSearchIn(params);
  const graphSlug = graphCollection || collections.data?.[0]?.slug || "";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <h1 className="mr-2 text-xl font-semibold">Export</h1>
        <ViewLinks params={params} current="export" />
      </div>
      <SearchSummary params={params} />
      {error && <p className="text-red-600" role="alert">{error}</p>}
      <section className={PANEL} aria-label="Export this search">
        <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">This search</h2>
        <label className="mb-3 block text-sm">
          the first{" "}
          <select aria-label="Documents" value={limit} onChange={(e) => setLimit(Number(e.target.value))} className={INPUT}>
            {[100, 1000, 10000].map((n) => (
              <option key={n} value={n}>
                {n.toLocaleString()}
              </option>
            ))}
          </select>{" "}
          documents
        </label>
        <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-sm">
          <dt className="text-zinc-500">Documents</dt>
          <dd className="flex flex-wrap gap-2">
            {TABLES.map((t) => (
              <button key={t.format} disabled={!!busy} className={BUTTON} onClick={() => run(`d${t.format}`, `/api/export/documents?format=${t.format}&limit=${limit}`, `semascope-documents.${t.format}`, search)}>
                {busy === `d${t.format}` ? "…" : t.label}
              </button>
            ))}
          </dd>
          <dt className="text-zinc-500">Entities and concepts</dt>
          <dd className="flex flex-wrap gap-2">
            {TABLES.map((t) => (
              <button key={t.format} disabled={!!busy} className={BUTTON} onClick={() => run(`e${t.format}`, `/api/export/entities?format=${t.format}&limit=${limit}`, `semascope-entities.${t.format}`, search)}>
                {busy === `e${t.format}` ? "…" : t.label}
              </button>
            ))}
          </dd>
          <dt className="text-zinc-500">RDF with tags and notes</dt>
          <dd className="flex flex-wrap gap-2">
            {RDF.map((r) => (
              <button key={r.format} disabled={!!busy} className={BUTTON} onClick={() => run(`r${r.format}`, `/api/export/search?format=${r.format}&limit=${limit}`, `semascope-search.${r.ext}`, search)}>
                {busy === `r${r.format}` ? "…" : r.label}
              </button>
            ))}
          </dd>
        </dl>
      </section>
      <section className={PANEL} aria-label="Export a collection's graph">
        <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">A collection's graph</h2>
        <p className="mb-2 text-sm text-zinc-500">Its documents with the concepts and entities they hold, as RDF.</p>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <select aria-label="Graph collection" value={graphSlug} onChange={(e) => setGraphCollection(e.target.value)} className={INPUT}>
            {collections.data?.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.name || c.slug}
              </option>
            ))}
          </select>
          {RDF.map((r) => (
            <button key={r.format} disabled={!!busy || !graphSlug} className={BUTTON} onClick={() => run(`g${r.format}`, `/api/export/graph?collection=${encodeURIComponent(graphSlug)}&format=${r.format}`, `semascope-graph-${graphSlug}.${r.ext}`)}>
              {busy === `g${r.format}` ? "…" : r.label}
            </button>
          ))}
        </div>
      </section>
      <AnnotationImport collections={collections.data?.map((c) => c.slug) ?? []} initial={params.collection} />
    </div>
  );
}

function AnnotationImport({ collections, initial }: { collections: string[]; initial?: string }) {
  const [collection, setCollection] = useState(initial ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ImportOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const target = collection || collections[0] || "";

  async function send(dryRun: boolean) {
    if (!file || !target) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await importAnnotations(target, file, dryRun));
    } catch (e) {
      setError(String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void send(true);
  }

  return (
    <section className={PANEL} aria-label="Import annotations">
      <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Import annotations</h2>
      <p className="mb-2 text-sm text-zinc-500">
        W3C Web Annotations or a Hypothesis export, as notes on the documents their targets name. Check first, then import; importing needs the curator role.
      </p>
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2 text-sm">
        <select aria-label="Import collection" value={target} onChange={(e) => setCollection(e.target.value)} className={INPUT}>
          {collections.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <input type="file" accept=".json,.jsonld,application/json,application/ld+json" aria-label="Annotation file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button disabled={!file || busy} className={BUTTON}>
          Check
        </button>
        <button type="button" disabled={!file || busy || !result?.dry_run} onClick={() => send(false)} className="rounded bg-blue-600 px-3 py-0.5 text-white disabled:opacity-40">
          Import
        </button>
      </form>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      {result && (
        <div className="mt-2 text-sm" aria-label="Import result">
          <p>
            {result.dry_run ? "Would import" : "Imported"} {result.imported} of {result.received} ({result.anchored} on a passage, {result.unanchored} on the whole document);{" "}
            {result.already_imported} already there, {result.unmatched} without a matching document.
          </p>
          {result.problems.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-xs text-zinc-500">
              {result.problems.map((p, i) => (
                <li key={i}>
                  {p.id}: {p.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

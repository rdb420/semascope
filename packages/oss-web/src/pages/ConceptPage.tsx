import { Link, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, curation, isRunning, type BulkTagIn, type Collection, type Concept } from "../api";
import { JobProgress } from "../components/JobProgress";
import { useUser } from "../components/Shell";

export function ConceptPage() {
  const { iri } = useSearch({ from: "/concepts" });
  const user = useUser();
  const concept = useQuery({ queryKey: ["concept", iri], queryFn: () => api.concept(iri), enabled: user.isSuccess && !!iri });
  const docs = useQuery({ queryKey: ["concept-docs", iri], queryFn: () => api.conceptDocuments(iri), enabled: user.isSuccess && !!iri });
  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const [label, setLabel] = useState("");
  const [reason, setReason] = useState("");
  const [collection, setCollection] = useState("");
  const propose = useMutation({
    mutationFn: () =>
      api.proposeDelta(concept.data!.pack_id, {
        collection: collection || collections.data?.[0]?.slug || "",
        kind: "add_label",
        params: { iri, label, label_type: "alt" },
        reason,
      }),
    onSuccess: () => {
      setLabel("");
      setReason("");
    },
  });
  function submitProposal(e: FormEvent) {
    e.preventDefault();
    if (label.trim()) propose.mutate();
  }
  if (!user.isSuccess) return null;
  if (!iri) return <p className="text-zinc-500">No concept selected.</p>;
  if (concept.isError) return <p className="text-red-600">{String(concept.error)}</p>;
  const c = concept.data;
  if (!c) return <p className="text-zinc-500">Loading…</p>;
  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[1fr_18rem]">
      <div className="space-y-4">
        <div>
          <Link to="/packs/$packId" params={{ packId: c.pack_id }} className="text-sm text-blue-700 hover:underline dark:text-blue-400">← {c.pack_id}</Link>
          <h1 className="text-xl font-semibold">{c.pref_label}</h1>
          <p className="text-xs text-zinc-500">{c.kind}{c.type_label ? ` · ${c.type_label}` : ""}{c.scheme_slug ? ` · ${c.scheme_slug}` : ""} · {c.iri}</p>
        </div>
        {c.definition && <p>{c.definition}</p>}
        <Link to="/graph" search={{ id: `${c.kind === "concept" ? "Concept" : "Entity"}:${c.iri}`, collection: "" }} className="text-sm text-blue-700 hover:underline dark:text-blue-400">
          Show in graph
        </Link>
        {(c.alt_labels.length > 0 || c.hidden_labels.length > 0) && (
          <p className="text-sm">
            <span className="text-zinc-500">Also: </span>
            {[...c.alt_labels, ...c.hidden_labels].join(", ")}
          </p>
        )}
        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Documents ({docs.data?.total ?? "…"})</h2>
          <ul className="space-y-2">
            {docs.data?.docs.map((d) => (
              <li key={d.id} className="rounded border border-zinc-200 bg-white p-3 text-sm dark:border-zinc-800 dark:bg-zinc-900">
                <Link to="/documents/$id" params={{ id: d.id }} className="font-medium text-blue-700 hover:underline dark:text-blue-400">{d.title_txt ?? d.uri_s}</Link>
                <p className="truncate text-xs text-zinc-500">{d.uri_s}</p>
              </li>
            ))}
          </ul>
        </section>
      </div>
      <aside className="space-y-3 text-sm">
        <dl className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="flex justify-between py-0.5"><dt className="text-zinc-500">Pack</dt><dd>{c.pack_id}@{c.pack_version}</dd></div>
          <div className="flex justify-between py-0.5"><dt className="text-zinc-500">Tags text</dt><dd>{c.taggable ? "yes" : "no"}</dd></div>
          {c.support && <div className="flex justify-between py-0.5"><dt className="text-zinc-500">Support</dt><dd>{c.support}</dd></div>}
          {c.term_status && <div className="flex justify-between py-0.5"><dt className="text-zinc-500">Status</dt><dd>{c.term_status}</dd></div>}
          {(c.paths?.length ? c.paths : c.path ? [c.path] : []).map((path, i) => (
            <div key={path} className="flex justify-between py-0.5"><dt className="text-zinc-500">{i === 0 ? "Path" : ""}</dt><dd className="truncate">{path}</dd></div>
          ))}
        </dl>
        {c.taggable && (
          <form onSubmit={submitProposal} className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Propose another name</h2>
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. a spelling a translation uses" className="mb-2 w-full rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700" />
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why (optional)" className="mb-2 w-full rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700" />
            <select value={collection} onChange={(e) => setCollection(e.target.value)} className="mb-2 w-full rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700">
              {collections.data?.map((col) => (
                <option key={col.slug} value={col.slug}>{col.name}</option>
              ))}
            </select>
            <button disabled={propose.isPending || !label.trim()} className="w-full rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700 disabled:opacity-50">
              {propose.isPending ? "Proposing…" : "Propose"}
            </button>
            {propose.isSuccess && (
              <p className="mt-2 text-xs text-green-600">
                Proposed. It tags this collection's documents as they are processed; those that already contain it are queued for retagging. It goes to the pack's next version as a draft.
              </p>
            )}
            {propose.isError && <p className="mt-2 text-xs text-red-600">{String(propose.error)}</p>}
          </form>
        )}
        {c.taggable && collections.data && collections.data.length > 0 && <BulkTag concept={c} collections={collections.data} />}
        {c.broader.length > 0 && (
          <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="mb-1 text-xs font-semibold uppercase text-zinc-500">Broader</h2>
            <ul>{c.broader.map((b) => <li key={b}><Link to="/concepts" search={{ iri: b }} className="text-blue-700 hover:underline dark:text-blue-400">{b.split(/[#/]/).pop()}</Link></li>)}</ul>
          </div>
        )}
        {(c.related ?? []).length > 0 && (
          <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="mb-1 text-xs font-semibold uppercase text-zinc-500">Related</h2>
            <ul>{(c.related ?? []).map((r) => <li key={r}><Link to="/concepts" search={{ iri: r }} className="text-blue-700 hover:underline dark:text-blue-400">{r.split(/[#/]/).pop()}</Link></li>)}</ul>
          </div>
        )}
      </aside>
    </div>
  );
}

/** Accept or reject this concept on every document a search finds (curators): count, then a job. */
function BulkTag({ concept, collections }: { concept: Concept; collections: Collection[] }) {
  const [collection, setCollection] = useState(collections[0]?.slug ?? "");
  const [q, setQ] = useState(`"${concept.pref_label.replace(/"/g, "")}"`);
  const [decision, setDecision] = useState<"accept" | "reject">("accept");
  const [reason, setReason] = useState("");
  const [jobId, setJobId] = useState<number | null>(null);
  const body: BulkTagIn = { collection, q, iri: concept.iri, decision, label: concept.pref_label, reason };
  const count = useMutation({ mutationFn: () => curation.bulkTagCount(body) });
  const start = useMutation({
    mutationFn: () => curation.bulkTag(body),
    onSuccess: (job) => setJobId(job.id),
  });
  const job = useQuery({
    queryKey: ["bulk-tag-job", jobId],
    queryFn: () => curation.bulkTagJob(jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data && !isRunning(query.state.data.status) ? false : 1500),
  });
  const reset = () => {
    count.reset();
    start.reset();
    setJobId(null);
  };
  const input = "mb-2 w-full rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700";
  const matched = count.data?.matched;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        count.mutate();
      }}
      className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
      aria-label="Tag the documents of a search"
    >
      <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Tag a search's documents</h2>
      <select value={collection} onChange={(e) => { setCollection(e.target.value); reset(); }} className={input} aria-label="Collection">
        {collections.map((col) => (
          <option key={col.slug} value={col.slug}>{col.name}</option>
        ))}
      </select>
      <input value={q} onChange={(e) => { setQ(e.target.value); reset(); }} placeholder="search words" className={input} aria-label="Search" />
      <div className="mb-2 flex gap-3">
        {(["accept", "reject"] as const).map((d) => (
          <label key={d}><input type="radio" name="bulk-decision" checked={decision === d} onChange={() => { setDecision(d); reset(); }} /> {d}</label>
        ))}
      </div>
      <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why (optional)" className={input} aria-label="Reason" />
      {matched === undefined ? (
        <button disabled={count.isPending || !collection} className="w-full rounded border px-3 py-1 disabled:opacity-50">
          {count.isPending ? "Counting…" : "Count documents"}
        </button>
      ) : jobId === null ? (
        <button
          type="button"
          onClick={() => start.mutate()}
          disabled={start.isPending || matched === 0 || matched > (count.data?.limit ?? 0)}
          className={`w-full rounded px-3 py-1 text-white disabled:opacity-50 ${decision === "accept" ? "bg-green-600 hover:bg-green-700" : "bg-red-600 hover:bg-red-700"}`}
        >
          {matched > (count.data?.limit ?? 0)
            ? `${matched} documents: narrow the search`
            : `${decision === "accept" ? "Accept" : "Reject"} on ${matched} document${matched === 1 ? "" : "s"}`}
        </button>
      ) : null}
      {job.data && (
        <JobProgress
          status={job.data.status}
          label={`${job.data.decision} on ${job.data.matched} documents`}
          detail={`${job.data.decided} decided, ${job.data.unchanged} already so${job.data.unapplied ? `, ${job.data.unapplied} waiting for a store` : ""}`}
          error={job.data.error}
        />
      )}
      {(count.isError || start.isError) && <p className="mt-2 text-xs text-red-600">{String(count.error ?? start.error)}</p>}
      <p className="mt-2 text-xs text-zinc-500">Each document gets its own decision in the log, as if decided on its page. Curators only.</p>
    </form>
  );
}

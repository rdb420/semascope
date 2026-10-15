import { Link, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../api";
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
          {c.path && <div className="flex justify-between py-0.5"><dt className="text-zinc-500">Path</dt><dd className="truncate">{c.path}</dd></div>}
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
            {propose.isSuccess && <p className="mt-2 text-xs text-green-600">Proposed. It tags this collection now and goes to the pack's next version as a draft.</p>}
            {propose.isError && <p className="mt-2 text-xs text-red-600">{String(propose.error)}</p>}
          </form>
        )}
        {c.broader.length > 0 && (
          <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="mb-1 text-xs font-semibold uppercase text-zinc-500">Broader</h2>
            <ul>{c.broader.map((b) => <li key={b}><Link to="/concepts" search={{ iri: b }} className="text-blue-700 hover:underline dark:text-blue-400">{b.split(/[#/]/).pop()}</Link></li>)}</ul>
          </div>
        )}
      </aside>
    </div>
  );
}

import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { analysis, type Candidate } from "../extrasApi";
import type { SynonymParams } from "../extrasParams";
import { useUser } from "../components/Shell";

const PANEL = "rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900";
const INPUT = "rounded border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700";

/** Synonyms: what a query expands to, and labels to propose for a concept. */
export function SynonymsPage() {
  const params = useSearch({ from: "/synonyms" }) as SynonymParams;
  const navigate = useNavigate({ from: "/synonyms" });
  const user = useUser();
  const [draft, setDraft] = useState(params.q ?? "");
  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const expand = useQuery({
    queryKey: ["expand", params.q, params.collection],
    queryFn: () => analysis.expand(params.q ?? "", params.collection),
    enabled: user.isSuccess && !!params.q,
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    navigate({ search: (s: SynonymParams) => ({ ...s, q: draft.trim() || undefined }) });
  }
  if (!user.isSuccess) return null;
  const x = expand.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Synonyms</h1>
        <select
          aria-label="Collection"
          value={params.collection ?? ""}
          onChange={(e) => navigate({ search: (s: SynonymParams) => ({ ...s, collection: e.target.value || undefined }) })}
          className={INPUT}
        >
          <option value="">Every collection</option>
          {collections.data?.map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.name || c.slug}
            </option>
          ))}
        </select>
      </div>
      <form onSubmit={submit} className="flex flex-wrap gap-2" aria-label="Test a query">
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="a query, e.g. Ulysses" aria-label="Query" className={`${INPUT} min-w-72 flex-1`} />
        <button className="rounded bg-blue-600 px-3 py-1 text-sm text-white">Show expansion</button>
      </form>
      {expand.isError && <p className="text-red-600">{String(expand.error)}</p>}
      {x && (
        <section className={PANEL} aria-label="Expansion">
          <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">What “{x.q}” expands to</h2>
          {x.matches.length === 0 && <p className="text-sm text-zinc-500">No word of it is a label of an active knowledge model.</p>}
          <ul className="space-y-2 text-sm">
            {x.matches.map((m) => (
              <li key={`${m.iri}${m.start}`}>
                <strong>{m.phrase}</strong> is the {m.matched_type} label of{" "}
                <Link to="/concepts" search={{ iri: m.iri }} className="text-blue-700 hover:underline dark:text-blue-400">
                  {m.pref_label}
                </Link>{" "}
                <span className="text-zinc-500">
                  ({m.kind}, {m.pack_id})
                </span>
                {m.expands_to.length > 0 ? (
                  <span>
                    {" "}
                    and also finds{" "}
                    {m.expands_to.map((e, i) => (
                      <span key={e.label}>
                        {i > 0 && ", "}
                        <em>{e.label}</em>
                        {e.weight < 1 && <span className="text-xs text-zinc-500"> ×{e.weight}</span>}
                      </span>
                    ))}
                  </span>
                ) : (
                  <span className="text-zinc-500"> and has no other label yet</span>
                )}
                <button
                  onClick={() => navigate({ search: (s: SynonymParams) => ({ ...s, iri: m.iri }) })}
                  className="ml-2 rounded border px-2 text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800"
                >
                  Suggest labels
                </button>
              </li>
            ))}
          </ul>
          {x.drafts.length > 0 && (
            <p className="mt-3 text-sm text-zinc-500">
              Proposed, waiting for a new version of the model: {x.drafts.map((d) => `${d.label} (${d.collection})`).join(", ")}
            </p>
          )}
          <p className="mt-3 text-xs text-zinc-500" aria-label="Search tokens">
            Search reads it as:{" "}
            {x.analysis_error
              ? "(the search engine's analysis is unavailable)"
              : x.tokens.map((t, i) => (
                  <code key={i} className={`mr-1 rounded px-1 ${t.type === "SYNONYM" ? "bg-blue-100 dark:bg-blue-900/40" : "bg-zinc-100 dark:bg-zinc-800"}`} title={`position ${t.position}, ${t.type}`}>
                    {t.text}
                  </code>
                ))}
          </p>
        </section>
      )}
      {params.iri && <Suggestions iri={params.iri} collection={params.collection} collections={collections.data?.map((c) => c.slug) ?? []} />}
    </div>
  );
}

function Suggestions({ iri, collection, collections }: { iri: string; collection?: string; collections: string[] }) {
  const qc = useQueryClient();
  const [target, setTarget] = useState(collection ?? "");
  const [done, setDone] = useState<Record<string, string>>({});
  const suggest = useQuery({ queryKey: ["suggest", iri, collection], queryFn: () => analysis.suggest(iri, collection) });
  const propose = useMutation({
    mutationFn: ({ c, type }: { c: Candidate; type: "alt" | "hidden" }) =>
      api.proposeDelta(suggest.data!.pack_id, {
        collection: target || collections[0],
        kind: "add_label",
        params: { iri, label: c.label, label_type: type },
        reason: `suggested: ${c.reasons.join(", ")}`,
      }),
    onSuccess: (_d, { c, type }) => {
      setDone((m) => ({ ...m, [c.label]: `proposed as ${type} label` }));
      qc.invalidateQueries({ queryKey: ["expand"] });
    },
    onError: (e, { c }) => setDone((m) => ({ ...m, [c.label]: String(e) })),
  });
  const s = suggest.data;
  return (
    <section className={PANEL} aria-label="Label suggestions">
      <h2 className="mb-2 text-xs font-semibold uppercase text-zinc-500">Labels to consider{s ? ` for ${s.pref_label}` : ""}</h2>
      {suggest.isLoading && <p className="text-sm text-zinc-500">Looking…</p>}
      {suggest.isError && <p className="text-red-600">{String(suggest.error)}</p>}
      {s && (
        <>
          <p className="mb-2 text-sm text-zinc-500">
            Its labels: {s.labels.join(", ")}. Tagged in {s.documents} documents.
          </p>
          <label className="mb-2 block text-sm">
            propose in{" "}
            <select aria-label="Proposing collection" value={target} onChange={(e) => setTarget(e.target.value)} className={INPUT}>
              {collections.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          {s.candidates.length === 0 && <p className="text-sm text-zinc-500">No candidates found.</p>}
          <table className="w-full text-sm">
            <tbody>
              {s.candidates.map((c) => (
                <tr key={c.label} className="border-t border-zinc-100 dark:border-zinc-800">
                  <td className="py-1">{c.label}</td>
                  <td className="text-xs text-zinc-500">
                    {c.reasons.join(", ")}
                    {c.distance != null && ` (${c.distance} edit${c.distance === 1 ? "" : "s"})`}
                    {c.lift != null && ` (×${c.lift})`}
                  </td>
                  <td className="text-xs tabular-nums text-zinc-500">{c.documents} docs</td>
                  <td className="text-right">
                    {done[c.label] ? (
                      <span className="text-xs text-zinc-500">{done[c.label]}</span>
                    ) : (
                      <>
                        <button onClick={() => propose.mutate({ c, type: "alt" })} className="rounded border px-2 text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800" title="a name searches should treat as this one">
                          Propose alt label
                        </button>{" "}
                        <button onClick={() => propose.mutate({ c, type: "hidden" })} className="rounded border px-2 text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800" title="a misspelling or OCR error: found, never shown">
                          hidden
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-zinc-500">A proposed label tags this collection's documents at once and becomes a search synonym when a version of the model that accepts it is activated. Proposing needs the curator role.</p>
        </>
      )}
    </section>
  );
}

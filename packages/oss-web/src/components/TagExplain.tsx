import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Fragment, type ReactNode } from "react";
import { api, type DocTag, type TagHistory, type TagSpan } from "../api";

const METHOD: Record<string, string> = {
  tag: "Dictionary match against the active packs' labels",
  ner: "Named-entity recognition",
  patterns: "Pattern match (regular expression)",
  curated: "Decided by a curator",
};

const LABEL_TYPE: Record<string, string> = {
  pref: "preferred label",
  alt: "alternative label",
  hidden: "hidden label",
  draft: "curator's draft label",
};

function confidenceReason(t: DocTag): string {
  if (t.stage === "tag") return "fixed by the type of label matched (the best one over all occurrences)";
  if (t.stage === "ner" && t.linked_from) return "the model's score, raised to at least 0.85 by linking to a pack individual";
  if (t.stage === "ner") return t.model?.startsWith("spacy:") ? "fixed for spaCy, which gives no score" : "the model's highest score over the occurrences";
  if (t.stage === "patterns") return "fixed for pattern matches";
  if (t.stage === "curated") return "a person decided";
  return "";
}

const DECISION: Record<string, string> = {
  accept_tag: "Accepted",
  add_tag: "Added",
  reject_tag: "Rejected",
  remove_tag: "Removed",
  link_entity: "Linked to an individual",
  dismiss_violation: "Dismissed a violation",
  defer: "Deferred",
};

type Entry = { at: string; who: string | null; what: string; note: string; detail?: string; inForce?: boolean };

/** One timeline from the proposals and the review decisions that did not become proposals. */
function timeline(h: TagHistory): Entry[] {
  const last = h.proposals.length ? h.proposals[h.proposals.length - 1].id : null;
  const entries: Entry[] = h.proposals.map((p) => {
    const evidence = p.params.tag;
    const linkTo = typeof p.params.iri === "string" ? ` → ${String(p.params.label || p.params.iri)}` : "";
    return {
      at: p.decided_at,
      who: p.decided_by,
      what: (DECISION[p.kind] ?? p.kind) + linkTo,
      note: p.reason,
      detail: evidence
        ? `on ${evidence.stage ?? "?"} evidence: confidence ${Number(evidence.confidence ?? 0).toFixed(2)}, ${String(
            (evidence as { span_count?: number }).span_count ?? evidence.spans?.length ?? 0,
          )} occurrences${p.pack_id ? `, ${p.pack_id}@${p.pack_version}` : ""}`
        : p.pack_id
          ? `${p.pack_id}@${p.pack_version}`
          : undefined,
      inForce: p.id === last,
    };
  });
  for (const r of h.reviews) {
    for (const d of r.decisions) {
      if (d.proposal_id === null) {
        entries.push({
          at: d.decided_at,
          who: d.decided_by,
          what: `${DECISION[d.decision] ?? d.decision} (review #${r.id})`,
          note: d.note,
        });
      }
    }
  }
  return entries.sort((a, b) => a.at.localeCompare(b.at));
}

function Field({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-2 py-0.5">
      <dt className="text-zinc-500">{k}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

export function TagExplain({
  docId,
  tag,
  occurrence,
  onOccurrence,
  onClose,
}: {
  docId: string;
  tag: DocTag;
  occurrence: number;
  onOccurrence: (i: number) => void;
  onClose: () => void;
}) {
  const history = useQuery({ queryKey: ["tag-history", docId, tag.iri], queryFn: () => api.tagHistory(docId, tag.iri) });
  const pages = [...new Set(tag.spans.map((s) => s.page).filter((p): p is number => p !== null))];
  const status = tag.rejected ? "rejected" : tag.curated?.decision === "accept" ? "accepted" : "not reviewed";
  const n = tag.spans.length;
  const entries = history.data ? timeline(history.data) : [];
  return (
    <section aria-label="Why this tag" className="rounded-lg border border-blue-200 bg-white p-4 dark:border-blue-900 dark:bg-zinc-900">
      <div className="mb-2 flex items-start gap-2">
        <h2 className="min-w-0 flex-1 text-xs font-semibold uppercase text-zinc-500">Why this tag</h2>
        <button onClick={onClose} title="close" className="rounded px-1 text-xs text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
          ✕
        </button>
      </div>
      <p className={`font-medium ${tag.rejected ? "text-zinc-400 line-through" : ""}`}>{tag.label}</p>
      <Link to="/concepts" search={{ iri: tag.iri }} className="block truncate text-xs text-blue-700 hover:underline dark:text-blue-400" title={tag.iri}>
        {tag.iri}
      </Link>
      <dl className="mt-3 text-xs">
        <Field k="Status">
          <span className={tag.rejected ? "text-red-600" : tag.curated?.decision === "accept" ? "text-green-600" : ""}>{status}</span>
          {tag.rejected && <span className="text-zinc-500"> · kept as evidence, left out of search and the graph</span>}
        </Field>
        <Field k="Method">
          {METHOD[tag.stage] ?? tag.stage}
          <span className="text-zinc-500">
            {" "}
            ({tag.stage}
            {tag.stage_version ? ` v${tag.stage_version}` : ""})
          </span>
        </Field>
        {tag.matched_label && (
          <Field k="Matched">
            “{tag.matched_label}”
            {tag.label_type && <span className="text-zinc-500"> · {LABEL_TYPE[tag.label_type] ?? tag.label_type}</span>}
            {tag.lang && <span className="text-zinc-500"> · {tag.lang}</span>}
          </Field>
        )}
        <Field k="Confidence">
          {tag.confidence.toFixed(2)} <span className="text-zinc-500">· {confidenceReason(tag)}</span>
        </Field>
        <Field k="Pack">{tag.pack_id ? `${tag.pack_id}@${tag.pack_version ?? "?"}` : <span className="text-zinc-500">none (not from a pack)</span>}</Field>
        {tag.model && <Field k="Model">{tag.model}</Field>}
        {tag.linked_from && (
          <Field k="Linked from">
            “{tag.linked_from.label}”
            <span className="text-zinc-500">
              {tag.linked_from.confidence !== null ? ` · score ${tag.linked_from.confidence.toFixed(2)}` : ""}
              {tag.linked_from.model ? ` · ${tag.linked_from.model}` : ""}
            </span>
            <span className="block truncate text-zinc-500" title={tag.linked_from.iri}>
              {tag.linked_from.iri}
            </span>
          </Field>
        )}
        <Field k="Where">
          {n === 0 ? (
            <span className="text-zinc-500">no recorded occurrence</span>
          ) : (
            <>
              {n} occurrence{n === 1 ? "" : "s"}
              {pages.length > 0 && <span className="text-zinc-500"> · page{pages.length === 1 ? "" : "s"} {pages.join(", ")}</span>}
              <span className="mt-1 flex flex-wrap items-center gap-1">
                <button onClick={() => onOccurrence((occurrence - 1 + n) % n)} className="rounded bg-zinc-100 px-1.5 hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700" title="previous occurrence">
                  ‹
                </button>
                <span className="whitespace-nowrap tabular-nums text-zinc-500">
                  {occurrence + 1} / {n}
                </span>
                <button onClick={() => onOccurrence((occurrence + 1) % n)} className="rounded bg-zinc-100 px-1.5 hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700" title="next occurrence">
                  ›
                </button>
                {tag.spans[occurrence]?.matched && <span className="basis-full text-zinc-500">this one matched “{tag.spans[occurrence].matched}”</span>}
              </span>
            </>
          )}
        </Field>
      </dl>
      <h3 className="mb-1 mt-3 text-xs font-semibold uppercase text-zinc-500">Decisions</h3>
      {history.isError && <p className="text-xs text-red-600">{String(history.error)}</p>}
      {history.data && entries.length === 0 && <p className="text-xs text-zinc-500">No one has decided on this tag yet.</p>}
      <ol className="space-y-1.5 text-xs">
        {entries.map((e, i) => (
          <li key={i} className="border-l-2 border-zinc-200 pl-2 dark:border-zinc-700">
            <span className="font-medium">{e.what}</span>
            {e.inForce && <span className="ml-1 rounded bg-zinc-100 px-1 text-[10px] uppercase text-zinc-500 dark:bg-zinc-800">in force</span>}
            <span className="block text-zinc-500">
              {e.who ?? "unknown"} · {new Date(e.at).toLocaleString()}
            </span>
            {e.note && <span className="block">“{e.note}”</span>}
            {e.detail && <span className="block text-zinc-500">{e.detail}</span>}
          </li>
        ))}
      </ol>
    </section>
  );
}

/** Text with the given spans (offsets relative to `base`) marked; the active one gets an id. */
export function Highlighted({ text, base, spans, active }: { text: string; base: number; spans: TagSpan[]; active: number }) {
  const inside = spans
    .map((s, i) => ({ start: s.start - base, end: s.end - base, i }))
    .filter((s) => s.end > 0 && s.start < text.length)
    .sort((a, b) => a.start - b.start);
  if (!inside.length) return <>{text}</>;
  const parts: ReactNode[] = [];
  let pos = 0;
  for (const s of inside) {
    const start = Math.max(s.start, pos);
    const end = Math.min(s.end, text.length);
    if (end <= start) continue;
    parts.push(<Fragment key={`t${s.i}`}>{text.slice(pos, start)}</Fragment>);
    parts.push(
      <mark
        key={`m${s.i}`}
        id={s.i === active ? "tag-occurrence" : undefined}
        className={`rounded-sm px-0.5 ${s.i === active ? "bg-amber-300 ring-2 ring-amber-500 dark:bg-amber-600" : "bg-amber-100 dark:bg-amber-900/60"} dark:text-zinc-50`}
      >
        {text.slice(start, end)}
      </mark>,
    );
    pos = end;
  }
  parts.push(<Fragment key="rest">{text.slice(pos)}</Fragment>);
  return <>{parts}</>;
}

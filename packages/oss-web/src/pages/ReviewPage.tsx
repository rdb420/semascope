import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ReviewItem } from "../api";
import { useUser } from "../components/Shell";

export function ReviewPage() {
  const user = useUser();
  const qc = useQueryClient();
  const [collection, setCollection] = useState("");
  const [kind, setKind] = useState("");
  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const summary = useQuery({ queryKey: ["review-summary", collection], queryFn: () => api.reviewSummary(collection || undefined), enabled: user.isSuccess });
  const items = useQuery({
    queryKey: ["reviews", collection, kind],
    queryFn: () => api.reviews({ collection: collection || undefined, kind: kind || undefined }),
    enabled: user.isSuccess,
  });
  const decide = useMutation({
    mutationFn: (args: { id: number; decision: string; note?: string; link_to?: string; label?: string }) =>
      api.decideReview(args.id, { decision: args.decision, note: args.note ?? "", link_to: args.link_to, label: args.label }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reviews"] });
      qc.invalidateQueries({ queryKey: ["review-summary"] });
    },
  });
  if (!user.isSuccess) return null;
  const total = Object.values(summary.data ?? {}).reduce((a, b) => a + b, 0);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Review queue</h1>
        <span className="text-sm text-zinc-500">{total} open</span>
        <select value={collection} onChange={(e) => setCollection(e.target.value)} className="rounded border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700">
          <option value="">All collections</option>
          {collections.data?.map((c) => (
            <option key={c.slug} value={c.slug}>{c.name}</option>
          ))}
        </select>
        <div className="flex gap-1 text-sm">
          {["", "link", "tag", "violation"].map((k) => (
            <button key={k} onClick={() => setKind(k)} className={`rounded border px-2 py-1 ${kind === k ? "bg-blue-100 dark:bg-blue-900/40" : ""}`}>
              {k || "all"} {k && summary.data?.[k] ? `(${summary.data[k]})` : ""}
            </button>
          ))}
        </div>
      </div>
      <p className="text-sm text-zinc-500">
        Machine proposals that need a person: entities the model found but could not link (accept with a pack individual, or reject), low-confidence tags, and SHACL violations. Every decision is logged and exported through the outbox.
      </p>
      <ul className="space-y-2">
        {items.data?.map((it) => (
          <ReviewRow key={it.id} item={it} busy={decide.isPending} onDecide={(d, extra) => decide.mutate({ id: it.id, decision: d, ...extra })} />
        ))}
        {items.data?.length === 0 && <li className="text-zinc-500">Nothing to review.</li>}
      </ul>
    </div>
  );
}

function ReviewRow({ item, busy, onDecide }: { item: ReviewItem; busy: boolean; onDecide: (decision: string, extra: { note?: string; link_to?: string; label?: string }) => void }) {
  const [linkTo, setLinkTo] = useState<string>((item.payload.candidates as { iri: string }[] | undefined)?.[0]?.iri ?? "");
  const candidates = (item.payload.candidates as { iri: string; label: string; type: string }[] | undefined) ?? [];
  return (
    <li className="rounded-lg border border-zinc-200 bg-white p-3 text-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs uppercase text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">{item.kind}</span>
        <span className="font-medium">{item.label || item.target}</span>
        {item.confidence != null && <span className="text-xs text-zinc-500">confidence {item.confidence.toFixed(2)}</span>}
        <span className="text-xs text-zinc-500">{String(item.payload.stage ?? "")}{item.payload.spans ? ` · ${String(item.payload.spans)} mentions` : ""}</span>
        {item.document_id && (
          <Link to="/documents/$id" params={{ id: item.document_id }} className="ml-auto truncate text-blue-700 hover:underline dark:text-blue-400">
            {item.document_title}
          </Link>
        )}
      </div>
      {item.kind === "violation" && <p className="mt-1 text-zinc-500">{String(item.payload.path ?? "")}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {item.kind === "link" && (
          <select value={linkTo} onChange={(e) => setLinkTo(e.target.value)} className="rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700">
            <option value="">link to…</option>
            {candidates.map((c) => (
              <option key={c.iri} value={c.iri}>{c.label} ({c.type})</option>
            ))}
          </select>
        )}
        <button disabled={busy || (item.kind === "link" && !linkTo)} onClick={() => onDecide("accept", item.kind === "link" ? { link_to: linkTo } : {})} className="rounded bg-green-600 px-3 py-1 text-white hover:bg-green-700 disabled:opacity-40">
          {item.kind === "violation" ? "Dismiss" : item.kind === "link" ? "Link" : "Accept"}
        </button>
        <button disabled={busy} onClick={() => onDecide("reject", {})} className="rounded bg-red-600 px-3 py-1 text-white hover:bg-red-700 disabled:opacity-40">
          Reject
        </button>
        <button disabled={busy} onClick={() => onDecide("defer", {})} className="rounded border px-3 py-1 disabled:opacity-40">
          Later
        </button>
      </div>
    </li>
  );
}

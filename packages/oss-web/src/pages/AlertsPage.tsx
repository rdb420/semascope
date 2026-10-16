import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, feedUrl, views, type AlertItem } from "../api";
import { useUser } from "../components/Shell";

/** Saved searches with their newest matches, alerts on and off, and their Atom/RSS feeds. */
export function AlertsPage() {
  const user = useUser();
  const qc = useQueryClient();
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: views.alerts, enabled: user.isSuccess });
  const saved = useQuery({ queryKey: ["saved"], queryFn: api.savedSearches, enabled: user.isSuccess });
  const token = useQuery({ queryKey: ["feed-token"], queryFn: views.feedToken, enabled: user.isSuccess });
  const [error, setError] = useState("");
  const onError = (e: unknown) => setError(String(e));
  const issue = useMutation({
    mutationFn: views.newFeedToken,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["feed-token"] }),
    onError,
  });
  const revoke = useMutation({
    mutationFn: views.revokeFeedToken,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["feed-token"] }),
    onError,
  });
  const toggle = useMutation({
    mutationFn: (item: AlertItem) => {
      const s = saved.data?.find((x) => x.id === item.id);
      if (!s) throw new Error("saved search not found");
      return api.saveSearch({ name: s.name, query: s.query, alert: !item.alert });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["saved"] });
    },
    onError,
  });

  if (!user.isSuccess) return null;
  const t = token.data?.token ?? null;
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Alerts and feeds</h1>
        <p className="text-sm text-zinc-500">
          Your saved searches with their newest documents. With alerts on, new matches also reach the event outbox and its webhooks. Every search is also a feed for a feed reader.
        </p>
      </div>
      {error && (
        <p role="alert" className="rounded bg-red-50 px-2 py-1 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
          {error}
        </p>
      )}
      <section aria-label="Feed token" className="rounded-lg border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900">
        <h2 className="mb-1 text-xs font-semibold uppercase text-zinc-500">Feed token</h2>
        <p className="mb-2 text-zinc-500">
          Feed readers cannot sign in, so feed links carry a token that opens your feeds and nothing else. Anyone with a link can read that feed: renew the token to cut every old link off.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {t ? (
            <>
              <FeedLinks label="All alerts" path="alerts" token={t} />
              <button onClick={() => issue.mutate()} className="rounded border px-2 py-0.5">
                Renew token
              </button>
              <button onClick={() => revoke.mutate()} className="rounded border px-2 py-0.5 text-red-700 dark:text-red-400">
                Revoke
              </button>
            </>
          ) : (
            <button onClick={() => issue.mutate()} className="rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700">
              Create feed token
            </button>
          )}
        </div>
      </section>
      {alerts.isError && <p className="text-red-600">{String(alerts.error)}</p>}
      {alerts.data && alerts.data.length === 0 && (
        <p className="text-sm text-zinc-500">
          No saved searches yet. Use <em>Save search</em> on the <Link to="/" className="text-blue-700 hover:underline dark:text-blue-400">search page</Link>.
        </p>
      )}
      <ul className="space-y-3">
        {alerts.data?.map((a) => {
          const s = saved.data?.find((x) => x.id === a.id);
          return (
            <li key={a.id} className="rounded-lg border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900">
              <div className="flex flex-wrap items-center gap-2">
                {s ? (
                  <Link
                    to="/"
                    search={{ q: s.query.q && s.query.q !== "*:*" ? s.query.q : undefined, collection: s.query.collection ?? undefined, f: s.query.filters && Object.keys(s.query.filters).length ? s.query.filters : undefined, mode: s.query.mode === "semantic" || s.query.mode === "hybrid" ? s.query.mode : undefined }}
                    className="font-medium text-blue-700 hover:underline dark:text-blue-400"
                  >
                    {a.name}
                  </Link>
                ) : (
                  <span className="font-medium">{a.name}</span>
                )}
                <span className="text-zinc-500">{a.total === null ? "" : `${a.total.toLocaleString()} document${a.total === 1 ? "" : "s"}`}</span>
                <label className="ml-auto flex items-center gap-1">
                  <input type="checkbox" checked={a.alert} disabled={toggle.isPending || !s} onChange={() => toggle.mutate(a)} />
                  alert
                </label>
                {t && <FeedLinks label="" path={`saved/${a.id}`} token={t} />}
              </div>
              {a.error && <p className="mt-1 text-red-600">{a.error}</p>}
              {a.newest.length > 0 && (
                <ol className="mt-2 space-y-0.5">
                  {a.newest.map((h) => (
                    <li key={h.id} className="flex gap-2">
                      <span className="w-24 shrink-0 tabular-nums text-zinc-500">{h.indexed_at?.slice(0, 10) ?? ""}</span>
                      <Link to="/documents/$id" params={{ id: h.id }} search={s?.query.q && s.query.q !== "*:*" ? { q: s.query.q } : {}} className="truncate hover:underline">
                        {h.title}
                      </Link>
                      <span className="text-zinc-400">{h.collection}</span>
                    </li>
                  ))}
                </ol>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function FeedLinks({ label, path, token }: { label: string; path: string; token: string }) {
  return (
    <span className="flex items-center gap-1">
      {label && <span className="text-zinc-500">{label}:</span>}
      <a href={feedUrl(path, token, "atom")} target="_blank" rel="noreferrer" className="rounded border border-orange-300 px-1.5 text-xs text-orange-700 hover:bg-orange-50 dark:border-orange-800 dark:text-orange-300 dark:hover:bg-orange-950">
        Atom
      </a>
      <a href={feedUrl(path, token, "rss")} target="_blank" rel="noreferrer" className="rounded border border-orange-300 px-1.5 text-xs text-orange-700 hover:bg-orange-50 dark:border-orange-800 dark:text-orange-300 dark:hover:bg-orange-950">
        RSS
      </a>
    </span>
  );
}

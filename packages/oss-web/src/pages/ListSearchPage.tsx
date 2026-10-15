import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useUser } from "../components/Shell";

export function ListSearchPage() {
  const user = useUser();
  const [names, setNames] = useState("");
  const [collection, setCollection] = useState("");
  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const run = useMutation({
    mutationFn: () =>
      api.listSearch(
        names.split(/\n|,/).map((n) => n.trim()).filter(Boolean),
        collection || undefined,
      ),
  });
  if (!user.isSuccess) return null;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Search a list of names</h1>
      <p className="text-sm text-zinc-500">One name per line (or comma-separated). Each is searched as a phrase; you get a count and the first hits.</p>
      <div className="flex gap-3">
        <textarea value={names} onChange={(e) => setNames(e.target.value)} rows={8} className="w-80 rounded border border-zinc-300 bg-white p-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" placeholder={"Zeus\nOdysseus\nMount Sinai"} />
        <div className="space-y-2">
          <select value={collection} onChange={(e) => setCollection(e.target.value)} className="block rounded border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700">
            <option value="">All collections</option>
            {collections.data?.map((c) => (
              <option key={c.slug} value={c.slug}>{c.name}</option>
            ))}
          </select>
          <button onClick={() => run.mutate()} disabled={run.isPending || !names.trim()} className="rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-40">
            {run.isPending ? "Searching…" : "Search"}
          </button>
        </div>
      </div>
      {run.data && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-zinc-500"><th className="py-1">Name</th><th>Documents</th><th>First hits</th></tr>
          </thead>
          <tbody>
            {Object.entries(run.data).map(([name, r]) => (
              <tr key={name} className="border-t border-zinc-200 dark:border-zinc-800">
                <td className="py-1 font-medium">{name}</td>
                <td>{r.total}</td>
                <td className="space-x-2">
                  {r.hits.map((h) => (
                    <Link key={h.id} to="/documents/$id" params={{ id: h.id }} className="text-blue-700 hover:underline dark:text-blue-400">{h.title}</Link>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

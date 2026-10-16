import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type ListSearchRow } from "../api";
import { useUser } from "../components/Shell";

type SortCol = "name" | "total" | "best_score" | "method";
const METHOD_LABEL: Record<string, string> = { phrase: "exact phrase", words: "all words", fuzzy: "near spelling" };
const METHOD_RANK: Record<string, number> = { phrase: 0, words: 1, fuzzy: 2 };

export function ListSearchPage() {
  const user = useUser();
  const [names, setNames] = useState("");
  const [collection, setCollection] = useState("");
  const [language, setLanguage] = useState("");
  const [loosen, setLoosen] = useState(true);
  const [sort, setSort] = useState<{ col: SortCol; desc: boolean }>({ col: "total", desc: true });
  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const run = useMutation({
    mutationFn: () =>
      api.listSearch(
        names.split(/\n|,/).map((n) => n.trim()).filter(Boolean),
        collection || undefined,
        {
          methods: loosen ? ["phrase", "words", "fuzzy"] : ["phrase"],
          filters: language ? { language_s: [language] } : {},
        },
      ),
  });
  const rows = useMemo(() => {
    const list: [string, ListSearchRow][] = Object.entries(run.data ?? {});
    const key = (r: [string, ListSearchRow]): string | number => {
      if (sort.col === "name") return r[0].toLowerCase();
      if (sort.col === "method") return r[1].method ? METHOD_RANK[r[1].method] ?? 9 : 9;
      return r[1][sort.col] ?? -1;
    };
    return list.sort((a, b) => {
      const x = key(a);
      const y = key(b);
      const c = x < y ? -1 : x > y ? 1 : 0;
      return sort.desc ? -c : c;
    });
  }, [run.data, sort]);
  if (!user.isSuccess) return null;

  function header(col: SortCol, label: string) {
    const active = sort.col === col;
    return (
      <th className="py-1" aria-sort={active ? (sort.desc ? "descending" : "ascending") : "none"}>
        <button onClick={() => setSort({ col, desc: active ? !sort.desc : col !== "name" })} className="hover:underline">
          {label}
          {active ? (sort.desc ? " ▼" : " ▲") : ""}
        </button>
      </th>
    );
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Search a list of names</h1>
      <p className="text-sm text-zinc-500">
        One name per line (or comma-separated). Each is searched as a phrase; a name the phrase does not find is tried with all its words, then with near spellings. You get a count, the best score and the first hits.
      </p>
      <div className="flex flex-wrap gap-3">
        <textarea value={names} onChange={(e) => setNames(e.target.value)} rows={8} className="w-80 rounded border border-zinc-300 bg-white p-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" placeholder={"Zeus\nOdysseus\nMount Sinai"} aria-label="Names" />
        <div className="space-y-2 text-sm">
          <select value={collection} onChange={(e) => setCollection(e.target.value)} className="block rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700" aria-label="Collection">
            <option value="">All collections</option>
            {collections.data?.map((c) => (
              <option key={c.slug} value={c.slug}>{c.name}</option>
            ))}
          </select>
          <input value={language} onChange={(e) => setLanguage(e.target.value.trim().toLowerCase())} placeholder="language code, e.g. en" className="block w-48 rounded border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700" aria-label="Only documents in this language" />
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={loosen} onChange={(e) => setLoosen(e.target.checked)} />
            also try all words, then near spellings
          </label>
          <button onClick={() => run.mutate()} disabled={run.isPending || !names.trim()} className="rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-40">
            {run.isPending ? "Searching…" : "Search"}
          </button>
        </div>
      </div>
      {run.isError && <p role="alert" className="text-sm text-red-600">{String(run.error)}</p>}
      {run.data && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-zinc-500">
              {header("name", "Name")}
              {header("total", "Documents")}
              {header("best_score", "Best score")}
              {header("method", "Matched by")}
              <th>First hits</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([name, r]) => (
              <tr key={name} className="border-t border-zinc-200 dark:border-zinc-800">
                <td className="py-1 font-medium">{name}</td>
                <td>{r.total}</td>
                <td>{r.best_score != null ? r.best_score.toFixed(2) : ""}</td>
                <td>{r.method ? METHOD_LABEL[r.method] ?? r.method : ""}</td>
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

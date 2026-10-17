import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { analysis, type DiffHunk } from "../extrasApi";
import type { DiffParams } from "../extrasParams";
import { useUser } from "../components/Shell";

const INPUT = "rounded border border-zinc-300 bg-transparent px-2 py-1 font-mono text-xs dark:border-zinc-700";
const DEL = "bg-red-100 text-red-900 dark:bg-red-950/60 dark:text-red-200";
const INS = "bg-green-100 text-green-900 dark:bg-green-950/60 dark:text-green-200";

function Lines({ lines, start, mark, sign }: { lines: string[]; start: number; mark: string; sign: string }) {
  return (
    <>
      {lines.map((line, i) => (
        <div key={i} className={`grid grid-cols-[3.5rem_1rem_1fr] ${mark}`}>
          <span className="select-none pr-2 text-right text-zinc-400">{start + i + 1}</span>
          <span className="select-none">{sign}</span>
          <span className="whitespace-pre-wrap break-words">{line || " "}</span>
        </div>
      ))}
    </>
  );
}

function Hunk({ h }: { h: DiffHunk }) {
  if (h.op === "skip") {
    return <div className="my-1 rounded bg-zinc-100 px-2 py-0.5 text-center text-zinc-500 dark:bg-zinc-800">{h.count} unchanged lines</div>;
  }
  if (h.op === "equal") return <Lines lines={h.lines ?? []} start={h.a_start} mark="" sign=" " />;
  if (h.op === "replace" && h.words) {
    return (
      <div className="my-1 border-l-4 border-amber-400 pl-2" data-op="replace">
        <span className="whitespace-pre-wrap break-words">
          {h.words.map((p, i) =>
            p.op === "equal" ? (
              <span key={i}>{p.text}</span>
            ) : p.op === "delete" ? (
              <del key={i} className={`${DEL} no-underline line-through`}>
                {p.text}
              </del>
            ) : (
              <ins key={i} className={`${INS} no-underline`}>
                {p.text}
              </ins>
            ),
          )}
        </span>
      </div>
    );
  }
  return (
    <div data-op={h.op}>
      {h.a && h.a.length > 0 && <Lines lines={h.a} start={h.a_start} mark={DEL} sign="−" />}
      {h.b && h.b.length > 0 && <Lines lines={h.b} start={h.b_start} mark={INS} sign="+" />}
    </div>
  );
}

/** Two documents compared: unchanged runs collapsed, changed lines marked word by word. */
export function DiffPage() {
  const params = useSearch({ from: "/diff" }) as DiffParams;
  const navigate = useNavigate({ from: "/diff" });
  const user = useUser();
  const [a, setA] = useState(params.a);
  const [b, setB] = useState(params.b);
  const similar = useQuery({
    queryKey: ["similar", params.a, "hybrid"],
    queryFn: () => api.similar(params.a, "hybrid"),
    enabled: user.isSuccess && !!params.a && !params.b,
  });
  const diff = useQuery({
    queryKey: ["diff", params.a, params.b],
    queryFn: () => analysis.diff(params.a, params.b),
    enabled: user.isSuccess && !!params.a && !!params.b,
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    navigate({ search: { a: a.trim(), b: b.trim() } });
  }
  if (!user.isSuccess) return null;
  const d = diff.data;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Compare two documents</h1>
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2 text-sm" aria-label="Documents to compare">
        <input aria-label="First document id" value={a} onChange={(e) => setA(e.target.value)} placeholder="first document id" className={`${INPUT} w-80`} />
        <input aria-label="Second document id" value={b} onChange={(e) => setB(e.target.value)} placeholder="second document id" className={`${INPUT} w-80`} />
        <button className="rounded bg-blue-600 px-3 py-1 text-white">Compare</button>
      </form>
      {params.a && !params.b && (
        <section aria-label="Pick the second document" className="text-sm">
          <p className="mb-1 text-zinc-500">Compare with a similar document:</p>
          {similar.isLoading && <p className="text-zinc-500">Looking…</p>}
          <ul className="space-y-1">
            {similar.data?.hits.map((h) => (
              <li key={h.id}>
                <Link to="/diff" search={{ a: params.a, b: h.id }} className="text-blue-700 hover:underline dark:text-blue-400">
                  {h.title || h.uri}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
      {diff.isLoading && <p className="text-sm text-zinc-500">Comparing…</p>}
      {diff.isError && <p className="text-red-600">{String(diff.error)}</p>}
      {d && (
        <>
          <p className="text-sm">
            <Link to="/documents/$id" params={{ id: d.a.id }} className="text-blue-700 hover:underline dark:text-blue-400">
              {d.a.title}
            </Link>{" "}
            →{" "}
            <Link to="/documents/$id" params={{ id: d.b.id }} className="text-blue-700 hover:underline dark:text-blue-400">
              {d.b.title}
            </Link>
            <span className="ml-3 text-zinc-500" aria-label="Diff summary">
              {Math.round(d.ratio * 100)}% alike · {d.added} added · {d.removed} removed · {d.changed} changed lines
              {d.truncated ? " · cut at the size limit" : ""}
            </span>
          </p>
          <section aria-label="Differences" className="overflow-x-auto rounded-lg border border-zinc-200 bg-white p-3 font-mono text-xs dark:border-zinc-800 dark:bg-zinc-900">
            {d.hunks.length === 0 && <p className="text-zinc-500">Both texts are empty.</p>}
            {d.hunks.map((h, i) => (
              <Hunk key={i} h={h} />
            ))}
          </section>
        </>
      )}
    </div>
  );
}

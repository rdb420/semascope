import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useUser } from "../components/Shell";

export function PacksPage() {
  const user = useUser();
  const packs = useQuery({ queryKey: ["packs"], queryFn: api.packs, enabled: user.isSuccess });
  if (!user.isSuccess) return null;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Knowledge models</h1>
      <p className="text-sm text-zinc-500">
        Packs are ontology-skill bundles. The active version of each pack tags and classifies documents.
      </p>
      <ul className="grid gap-3 md:grid-cols-2">
        {packs.data?.map((p) => (
          <li key={p.id} className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex items-baseline justify-between">
              <Link to="/packs/$packId" params={{ packId: p.pack_id }} className="font-medium text-blue-700 hover:underline dark:text-blue-400">
                {p.title}
              </Link>
              <span className={`text-xs ${p.status === "active" ? "text-green-600" : "text-zinc-500"}`}>{p.status}</span>
            </div>
            <p className="text-xs text-zinc-500">
              {p.pack_id}@{p.version} · bundle {p.bundle_status}
            </p>
            <p className="mt-2 text-sm">
              {p.counts.concepts ?? 0} concepts · {p.counts.individuals ?? 0} individuals · {p.counts.classes ?? 0} classes ·{" "}
              {p.counts.triples ?? 0} triples
            </p>
            {p.cq_results?.tests ? (
              <p className="text-xs text-zinc-500">
                competency tests {p.cq_results.passed}/{p.cq_results.tests}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

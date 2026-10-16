import type { PackPlan } from "./api";

/** What a plan says in one line: the version change and the collections a switch reindexes. */
export function planSummary(plan: PackPlan): string {
  const s = plan.diff?.summary ?? {};
  const counts = (key: string) => Object.values((s[key] as Record<string, number>) ?? {}).reduce((a, b) => a + b, 0);
  const reindex = plan.plan?.reindex ?? [];
  return (
    `${plan.active_version ?? "nothing"} → ${plan.candidate_version}: ` +
    `${counts("added")} added, ${counts("removed")} removed, ${counts("changed")} changed` +
    (reindex.length ? `; reindexes ${reindex.join(", ")}` : "")
  );
}

import { describe, expect, it } from "vitest";
import { isRunning, type PackPlan } from "../api";
import { planSummary } from "../jobs";

describe("planSummary", () => {
  it("says what a pack switch changes and reindexes", () => {
    const plan: PackPlan = {
      pack_id: "demo",
      active_version: "0.1.0",
      candidate_version: "0.2.0",
      requires_confirmation: true,
      diff: { summary: { added: { concept: 2 }, removed: { concept: 1, individual: 1 }, changed: {} } },
      plan: { reindex: ["texts", "mail"] },
    };
    expect(planSummary(plan)).toBe("0.1.0 → 0.2.0: 2 added, 2 removed, 0 changed; reindexes texts, mail");
  });

  it("copes with a first import and no reindex", () => {
    const plan: PackPlan = { pack_id: "x", active_version: null, candidate_version: "1", requires_confirmation: false };
    expect(planSummary(plan)).toBe("nothing → 1: 0 added, 0 removed, 0 changed");
  });
});

describe("isRunning", () => {
  it("is true until a job is done or failed", () => {
    expect(isRunning("queued") && isRunning("running")).toBe(true);
    expect(isRunning("done") || isRunning("failed")).toBe(false);
  });
});

import { describe, expect, it } from "vitest";
import { analysisSearchSchema, diffSearchSchema, explainSearch, explainSearchSchema, synonymsSearchSchema } from "../extrasParams";

describe("ADR-0012 page parameters", () => {
  it("keeps the search and the analysis tab's own settings", () => {
    const p = analysisSearchSchema({ q: "flood", collection: "demo", tab: "tree", word: "wine-dark", side: "left", k: "4", vs: "war" });
    expect(p).toEqual({ q: "flood", collection: "demo", tab: "tree", word: "wine-dark", side: "left", k: 4, vs: "war" });
  });

  it("drops what it does not know", () => {
    expect(analysisSearchSchema({ tab: "nonsense", side: "up", k: 99 })).toEqual({});
    expect(diffSearchSchema({ a: "x" })).toEqual({ a: "x", b: "" });
    expect(synonymsSearchSchema({ q: "", iri: "https://p#c", other: 1 })).toEqual({ iri: "https://p#c" });
  });

  it("explains the search as the search page sends it", () => {
    const p = explainSearchSchema({ q: "odysseus", mode: "hybrid", op: "all", stem: "false", doc: "d1", f: { language_s: ["en"] } });
    expect(p.doc).toBe("d1");
    expect(explainSearch(p)).toMatchObject({
      q: "odysseus",
      mode: "hybrid",
      operator: "all",
      stemming: false,
      filters: { language_s: ["en"] },
      facets: [],
    });
    expect(explainSearch(explainSearchSchema({ q: "x" }))).toMatchObject({ operator: null, stemming: true, mode: "keyword" });
  });
});

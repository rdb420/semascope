import { describe, expect, it } from "vitest";
import {
  barRange,
  bucketLabel,
  buildTree,
  clean,
  facetTitle,
  fromSaved,
  histogramGap,
  rangeLabel,
  searchSearchSchema,
  sortKey,
  suggestTerm,
  suggestionQuery,
  toPayload,
  toSaved,
  toggleValue,
} from "../searchState";

describe("search parameters in the URL", () => {
  it("keeps a numeric query: the router hands ?q=1984 over as a number", () => {
    expect(searchSearchSchema({ q: 1984 }).q).toBe("1984");
    expect(searchSearchSchema({ q: 0 }).q).toBe("0");
    expect(searchSearchSchema({ collection: 2024 }).collection).toBe("2024");
  });

  it("keeps filter values that look like numbers as text", () => {
    expect(searchSearchSchema({ f: { year_s: [1984, "x"] } }).f).toEqual({ year_s: ["1984", "x"] });
    expect(searchSearchSchema({ x: { language_s: "de" } }).x).toEqual({ language_s: ["de"] });
    expect(searchSearchSchema({ f: ["a"] }).f).toBeUndefined();
  });

  it("reads sort, operator, stemming and dates, and drops what it does not know", () => {
    expect(searchSearchSchema({ sort: "newest", op: "any", stem: false, from: "1920-01-01", to: 1921 })).toEqual({
      sort: "newest",
      op: "any",
      stem: false,
      from: "1920-01-01",
      to: "1921",
    });
    expect(searchSearchSchema({ sort: "modified_dt asc" }).sort).toBe("oldest"); // the table view's sort
    expect(searchSearchSchema({ sort: "bogus", op: "maybe", stem: true, from: "yesterday" })).toEqual({});
    expect(sortKey("relevance")).toBe("relevance");
  });

  it("cleans defaults out of the URL", () => {
    expect(clean({ q: "", page: 1, view: "list", sort: "relevance", f: {}, x: {} })).toEqual({});
  });
});

describe("the API payload", () => {
  it("carries every option", () => {
    const p = toPayload(
      { q: "Kinder", f: { language_s: ["de"] }, x: { persons_ss: ["Zeus"] }, sort: "newest", op: "all", stem: false, from: "1920-01-01", to: "1921-01-01", page: 2 },
      ["language_s"],
      ["persons_ss"],
    );
    expect(p).toMatchObject({
      q: "Kinder",
      filters: { language_s: ["de"] },
      exclude: { persons_ss: ["Zeus"] },
      facet_more: ["persons_ss"],
      sort: "modified_dt desc,indexed_dt desc",
      operator: "all",
      stemming: false,
      page: 2,
      page_size: 10,
      histogram_gap: "+1MONTH",
      dates: { field: "modified_dt", start: "1920-01-01", end: "1921-01-01" },
    });
  });

  it("pages the media view by its own page size", () => {
    const p = toPayload({ view: "media" }, []);
    expect(p.page_size).toBe(40);
    expect(p.filters).toEqual({ content_type_group_s: ["image"] });
    expect(toPayload({}, []).q).toBe("*:*");
  });

  it("restores a saved search with its mode, view and options", () => {
    const params = { q: "flood", mode: "hybrid" as const, view: "media" as const, sort: "oldest", f: { language_s: ["en"] }, stem: false as const };
    const saved = toSaved(params, ["language_s"]);
    expect(saved.view).toBe("media");
    expect(fromSaved(saved)).toEqual(params);
    // saved before views were kept: the plain payload
    expect(fromSaved({ q: "*:*", filters: { a_s: ["b"] }, mode: "keyword" })).toEqual({ f: { a_s: ["b"] } });
  });
});

describe("filters", () => {
  it("toggles a value and drops empty fields", () => {
    expect(toggleValue(undefined, "a_s", "x")).toEqual({ a_s: ["x"] });
    expect(toggleValue({ a_s: ["x"] }, "a_s", "x")).toBeUndefined();
    expect(toggleValue({ a_s: ["x"], b_s: ["y"] }, "a_s", "x")).toEqual({ b_s: ["y"] });
  });
});

describe("date drill-down", () => {
  it("narrows the bars from years to months to days", () => {
    expect(histogramGap()).toBe("+1YEAR");
    expect(histogramGap("1920-01-01", "1921-01-01")).toBe("+1MONTH");
    expect(histogramGap("1920-03-01", "1920-04-01")).toBe("+1DAY");
    expect(histogramGap("1900-01-01", "1950-01-01")).toBe("+1YEAR");
  });

  it("turns a clicked bar into its range", () => {
    expect(barRange("1920-01-01", "+1YEAR")).toEqual({ from: "1920-01-01", to: "1921-01-01" });
    expect(barRange("1920-12-01", "+1MONTH")).toEqual({ from: "1920-12-01", to: "1921-01-01" });
    expect(barRange("1920-02-28", "+1DAY")).toEqual({ from: "1920-02-28", to: "1920-02-29" });
  });

  it("names a range and a bar", () => {
    expect(rangeLabel("1920-01-01", "1921-01-01")).toBe("1920");
    expect(rangeLabel("1920-03-01", "1920-04-01")).toBe("1920-03");
    expect(rangeLabel("1920-03-05", "1920-03-06")).toBe("1920-03-05");
    expect(rangeLabel("1920-03-05", undefined)).toBe("1920-03-05 – …");
    expect(bucketLabel("1920-03-01", "+1MONTH")).toBe("1920-03");
    expect(bucketLabel("1920-01-01", "+1YEAR")).toBe("1920");
  });
});

describe("facets and suggestions", () => {
  it("names facets, path fields included", () => {
    expect(facetTitle("phones_ss")).toBe("Phone numbers");
    expect(facetTitle("folder_paths")).toBe("Folder");
    expect(facetTitle("x_deity_entities_ss")).toBe("Deity (found)");
  });

  it("builds a folder tree from ancestor-expanded paths", () => {
    const tree = buildTree([
      { value: "a", count: 3 },
      { value: "a/b", count: 2 },
      { value: "a/b/c", count: 1 },
      { value: "d", count: 1 },
    ]);
    expect(tree.map((n) => n.label)).toEqual(["a", "d"]);
    expect(tree[0].children[0].children[0].value).toBe("a/b/c");
  });

  it("suggests only for plain words, and picks a title as a phrase", () => {
    expect(suggestTerm("odys")).toBe("odys");
    expect(suggestTerm("o")).toBe("");
    expect(suggestTerm("title:odys")).toBe("");
    expect(suggestTerm('"odys')).toBe("");
    expect(suggestionQuery('The "Odyssey"  of Homer')).toBe('"The Odyssey of Homer"');
  });
});

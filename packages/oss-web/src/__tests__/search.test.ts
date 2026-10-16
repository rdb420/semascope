import { describe, expect, it } from "vitest";
import { sanitiseSnippet, searchSearchSchema } from "../pages/SearchPage";

describe("sanitiseSnippet", () => {
  it("keeps the highlight tags Solr adds", () => {
    expect(sanitiseSnippet("the <mark>wine</mark>-dark sea")).toBe("the <mark>wine</mark>-dark sea");
  });

  it("escapes every other tag, so a snippet cannot inject markup", () => {
    expect(sanitiseSnippet('<img src=x onerror="alert(1)"><mark>x</mark>')).toBe(
      '&lt;img src=x onerror="alert(1)"&gt;<mark>x</mark>',
    );
    expect(sanitiseSnippet("<script>alert(1)</script>")).toBe("&lt;script&gt;alert(1)&lt;/script&gt;");
  });

  it("escapes ampersands before tags, so entities are not decoded twice", () => {
    expect(sanitiseSnippet("&lt;b&gt;")).toBe("&amp;lt;b&amp;gt;");
  });
});

describe("searchSearchSchema", () => {
  it("returns an object for an empty query string", () => {
    expect(searchSearchSchema({})).toBeTypeOf("object");
  });

  it("keeps a text query", () => {
    expect(searchSearchSchema({ q: "odyssey" }).q).toBe("odyssey");
  });

  it("drops values the page does not understand", () => {
    expect(searchSearchSchema({ view: "bogus", mode: "fuzzy", page: 1, q: "" })).toEqual({});
  });
});

import { describe, expect, it } from "vitest";
import { router } from "../router";

describe("router", () => {
  it("serves every page of the UI", () => {
    const paths = Object.keys(router.routesByPath).sort();
    expect(paths).toEqual(
      ["/", "/concepts", "/documents/$id", "/graph", "/list", "/login", "/map", "/packs", "/packs/$packId", "/review"].sort(),
    );
  });

  it("loads every page except search on demand", () => {
    for (const route of Object.values(router.routesById)) {
      if (route.isRoot) continue;
      const lazy = typeof (route.options.component as { preload?: unknown } | undefined)?.preload === "function";
      expect([route.fullPath, lazy]).toEqual([route.fullPath, route.fullPath !== "/"]);
    }
  });
});

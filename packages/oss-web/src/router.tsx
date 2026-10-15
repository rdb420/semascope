import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router";
import { Shell } from "./components/Shell";
import { SearchPage, searchSearchSchema } from "./pages/SearchPage";
import { LoginPage } from "./pages/LoginPage";
import { DocumentPage } from "./pages/DocumentPage";
import { PacksPage } from "./pages/PacksPage";
import { PackPage } from "./pages/PackPage";
import { ConceptPage } from "./pages/ConceptPage";

const rootRoute = createRootRoute({
  component: () => (
    <Shell>
      <Outlet />
    </Shell>
  ),
});

const searchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  validateSearch: searchSearchSchema,
  component: SearchPage,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
});

const documentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/documents/$id",
  component: DocumentPage,
});

const packsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/packs", component: PacksPage });
const packRoute = createRoute({ getParentRoute: () => rootRoute, path: "/packs/$packId", component: PackPage });
const conceptRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/concepts",
  validateSearch: (raw: Record<string, unknown>) => ({ iri: typeof raw.iri === "string" ? raw.iri : "" }),
  component: ConceptPage,
});

const routeTree = rootRoute.addChildren([searchRoute, loginRoute, documentRoute, packsRoute, packRoute, conceptRoute]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

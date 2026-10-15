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
import { GraphPage } from "./pages/GraphPage";
import { ReviewPage } from "./pages/ReviewPage";
import { MapPage } from "./pages/MapPage";
import { ListSearchPage } from "./pages/ListSearchPage";

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

const graphRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/graph",
  validateSearch: (raw: Record<string, unknown>) => ({
    id: typeof raw.id === "string" ? raw.id : "",
    collection: typeof raw.collection === "string" ? raw.collection : "",
  }),
  component: GraphPage,
});

const reviewRoute = createRoute({ getParentRoute: () => rootRoute, path: "/review", component: ReviewPage });

const mapRoute = createRoute({ getParentRoute: () => rootRoute, path: "/map", component: MapPage });

const listRoute = createRoute({ getParentRoute: () => rootRoute, path: "/list", component: ListSearchPage });

const routeTree = rootRoute.addChildren([searchRoute, loginRoute, documentRoute, packsRoute, packRoute, conceptRoute, graphRoute, reviewRoute, mapRoute, listRoute]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

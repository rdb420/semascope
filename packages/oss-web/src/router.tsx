import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
} from "@tanstack/react-router";
import { Shell } from "./components/Shell";
import { SearchPage, searchSearchSchema } from "./pages/SearchPage";

// Search is the landing page and ships in the entry chunk. Every other page is
// its own chunk, loaded on first visit (or on hover over a link to it), so the
// map (maplibre), the graph (cytoscape) and the rest stay off the first load.
const LoginPage = lazyRouteComponent(() => import("./pages/LoginPage"), "LoginPage");
const DocumentPage = lazyRouteComponent(() => import("./pages/DocumentPage"), "DocumentPage");
const PacksPage = lazyRouteComponent(() => import("./pages/PacksPage"), "PacksPage");
const PackPage = lazyRouteComponent(() => import("./pages/PackPage"), "PackPage");
const ConceptPage = lazyRouteComponent(() => import("./pages/ConceptPage"), "ConceptPage");
const GraphPage = lazyRouteComponent(() => import("./pages/GraphPage"), "GraphPage");
const ReviewPage = lazyRouteComponent(() => import("./pages/ReviewPage"), "ReviewPage");
const MapPage = lazyRouteComponent(() => import("./pages/MapPage"), "MapPage");
const ListSearchPage = lazyRouteComponent(() => import("./pages/ListSearchPage"), "ListSearchPage");

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

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

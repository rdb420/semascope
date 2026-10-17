import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
} from "@tanstack/react-router";
import { Shell } from "./components/Shell";
import { SearchPage, searchSearchSchema } from "./pages/SearchPage";
import { viewSearchSchema } from "./viewParams";
import { analysisSearchSchema, diffSearchSchema, explainSearchSchema, synonymsSearchSchema } from "./extrasParams";

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
const TablePage = lazyRouteComponent(() => import("./pages/TablePage"), "TablePage");
const EntitiesPage = lazyRouteComponent(() => import("./pages/EntitiesPage"), "EntitiesPage");
const AlertsPage = lazyRouteComponent(() => import("./pages/AlertsPage"), "AlertsPage");
const SearchGraphPage = lazyRouteComponent(() => import("./pages/GraphPage"), "SearchGraphPage");
const AnalyticsPage = lazyRouteComponent(() => import("./pages/AnalyticsPage"), "AnalyticsPage");
const AnalysisPage = lazyRouteComponent(() => import("./pages/AnalysisPage"), "AnalysisPage");
const DiffPage = lazyRouteComponent(() => import("./pages/DiffPage"), "DiffPage");
const SynonymsPage = lazyRouteComponent(() => import("./pages/SynonymsPage"), "SynonymsPage");
const ExplainPage = lazyRouteComponent(() => import("./pages/ExplainPage"), "ExplainPage");
const ExportPage = lazyRouteComponent(() => import("./pages/ExportPage"), "ExportPage");

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

const analyticsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/analytics", component: AnalyticsPage });

// views of a search (table, entities, co-occurrence graph) and the alert list
const tableRoute = createRoute({ getParentRoute: () => rootRoute, path: "/table", validateSearch: viewSearchSchema, component: TablePage });
const entitiesRoute = createRoute({ getParentRoute: () => rootRoute, path: "/entities", validateSearch: viewSearchSchema, component: EntitiesPage });
const searchGraphRoute = createRoute({ getParentRoute: () => rootRoute, path: "/graph/results", validateSearch: viewSearchSchema, component: SearchGraphPage });
const alertsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/alerts", component: AlertsPage });
const viewRoutes = [tableRoute, entitiesRoute, searchGraphRoute, alertsRoute];

// ADR-0012 pages: analysis views of a search, a diff of two documents, synonyms, why a hit
// ranked, exports and the annotation import
const analysisRoute = createRoute({ getParentRoute: () => rootRoute, path: "/analysis", validateSearch: analysisSearchSchema, component: AnalysisPage });
const diffRoute = createRoute({ getParentRoute: () => rootRoute, path: "/diff", validateSearch: diffSearchSchema, component: DiffPage });
const synonymsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/synonyms", validateSearch: synonymsSearchSchema, component: SynonymsPage });
const explainRoute = createRoute({ getParentRoute: () => rootRoute, path: "/explain", validateSearch: explainSearchSchema, component: ExplainPage });
const exportRoute = createRoute({ getParentRoute: () => rootRoute, path: "/export", validateSearch: viewSearchSchema, component: ExportPage });
const extraRoutes = [analysisRoute, diffRoute, synonymsRoute, explainRoute, exportRoute];

const routeTree = rootRoute.addChildren([searchRoute, loginRoute, documentRoute, packsRoute, packRoute, conceptRoute, graphRoute, reviewRoute, mapRoute, listRoute, ...viewRoutes, analyticsRoute, ...extraRoutes]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

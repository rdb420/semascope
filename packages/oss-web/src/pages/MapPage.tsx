import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Map as MapLibreMap, Marker, setWorkerUrl } from "maplibre-gl";
import type { Marker as MarkerType, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

// The worker and its shared chunk are copied beside index.html by vite.config.ts.
setWorkerUrl("/maplibre-gl-worker.mjs");
import { api } from "../api";
import type { UiConfig } from "../api";
import { useUser } from "../components/Shell";

// The base map comes from SEMASCOPE_MAP_TILE_URL (an XYZ raster template). Empty means no
// base map, for offline installs: the places are drawn on a plain background.
function mapStyle(config: UiConfig): StyleSpecification {
  if (!config.map_tile_url) {
    return { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#e4e4e7" } }] };
  }
  return {
    version: 8,
    sources: {
      base: {
        type: "raster",
        tiles: [config.map_tile_url],
        tileSize: 256,
        attribution: config.map_tile_attribution,
      },
    },
    layers: [{ id: "base", type: "raster", source: "base" }],
  };
}

export function MapPage() {
  const user = useUser();
  const navigate = useNavigate();
  const [collection, setCollection] = useState("");
  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections, enabled: user.isSuccess });
  const places = useQuery({ queryKey: ["places", collection], queryFn: () => api.places(collection || undefined), enabled: user.isSuccess });
  const config = useQuery({ queryKey: ["ui-config"], queryFn: api.uiConfig, enabled: user.isSuccess, staleTime: Infinity });
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !places.data || !config.data) return;
    const map = new MapLibreMap({ container: ref.current, style: mapStyle(config.data), center: [20, 35], zoom: 2.5 });
    const markers: MarkerType[] = [];
    for (const p of places.data) {
      const el = document.createElement("button");
      el.className = "rounded-full border-2 border-white bg-blue-600 shadow";
      const size = 10 + Math.min(20, p.docs * 2);
      el.style.width = `${size}px`;
      el.style.height = `${size}px`;
      el.title = `${p.label} · ${p.docs} documents`;
      el.onclick = () => navigate({ to: "/concepts", search: { iri: p.iri } });
      markers.push(new Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map));
    }
    return () => {
      markers.forEach((m) => m.remove());
      map.remove();
    };
  }, [places.data, config.data, navigate]);
  if (!user.isSuccess) return null;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold">Map</h1>
        <select value={collection} onChange={(e) => setCollection(e.target.value)} className="rounded border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700">
          <option value="">All collections</option>
          {collections.data?.map((c) => (
            <option key={c.slug} value={c.slug}>{c.name}</option>
          ))}
        </select>
        <span className="text-sm text-zinc-500">
          {places.data?.length ?? 0} places from the active packs; marker size is the number of documents
          {config.data && !config.data.map_tile_url ? "; no base map is configured (SEMASCOPE_MAP_TILE_URL)" : ""}
        </span>
      </div>
      <div ref={ref} className="h-[70vh] w-full rounded-lg border border-zinc-200 dark:border-zinc-800" />
    </div>
  );
}

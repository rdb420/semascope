import { useEffect, useRef } from "react";
import cytoscape, { type ElementDefinition } from "cytoscape";
import type { GraphOut } from "../api";

const COLOURS: Record<string, string> = {
  Document: "#2563eb",
  Concept: "#ca8a04",
  Entity: "#16a34a",
  Source: "#64748b",
  Collection: "#64748b",
  Pack: "#9333ea",
};

export function GraphView({
  graph,
  onSelect,
  height = 520,
}: {
  graph: GraphOut;
  onSelect?: (id: string, kind: string) => void;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const elements: ElementDefinition[] = [
      ...graph.nodes.map((n) => ({ data: { id: n.id, label: n.label, kind: n.kind } })),
      ...graph.edges.map((e) => ({ data: { id: `${e.source}|${e.type}|${e.target}`, source: e.source, target: e.target, type: e.type } })),
    ];
    const cy = cytoscape({
      container: ref.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-size": 9,
            "text-wrap": "ellipsis",
            "text-max-width": "110px",
            "text-valign": "bottom",
            "text-margin-y": 3,
            width: 14,
            height: 14,
            "background-color": (ele) => COLOURS[ele.data("kind") as string] ?? "#999",
            color: "#444",
          },
        },
        { selector: "node[kind = 'Document']", style: { shape: "rectangle" } },
        { selector: "node[kind = 'Concept']", style: { shape: "round-rectangle" } },
        { selector: "node:selected", style: { "border-width": 3, "border-color": "#111" } },
        {
          selector: "edge",
          style: { width: 1, "line-color": "#cbd5e1", "curve-style": "haystack", opacity: 0.8 },
        },
        { selector: "edge[type = 'CURATED']", style: { "line-color": "#f97316", width: 2 } },
      ],
      layout: { name: "cose", animate: false, nodeRepulsion: () => 8000, idealEdgeLength: () => 60, padding: 10 },
      wheelSensitivity: 0.2,
    });
    cy.on("tap", "node", (evt) => onSelect?.(evt.target.id(), evt.target.data("kind")));
    return () => {
      cy.destroy();
    };
  }, [graph, onSelect]);
  return <div ref={ref} style={{ height }} className="w-full rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900" />;
}

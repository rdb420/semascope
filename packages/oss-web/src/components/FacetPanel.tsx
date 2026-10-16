import { useState } from "react";
import type { FacetValue } from "../api";
import { FACET_COLLAPSED, buildTree, facetTitle, rangeLabel, type Filters, type TreeNode } from "../searchState";

type Handlers = {
  onToggle: (field: string, value: string) => void;
  onExclude: (field: string, value: string) => void;
};

const row = "flex w-full items-center justify-between rounded py-0.5 pr-1 text-left hover:bg-zinc-200 dark:hover:bg-zinc-800";
const on = "bg-blue-100 font-medium dark:bg-blue-900/40";
const off = "bg-red-50 text-zinc-500 line-through dark:bg-red-950/40";

function ExcludeButton({ field, value, excluded, onExclude }: { field: string; value: string; excluded: boolean; onExclude: Handlers["onExclude"] }) {
  return (
    <button
      onClick={() => onExclude(field, value)}
      title={excluded ? `include ${value} again` : `leave out ${value}`}
      aria-label={excluded ? `include ${value} again` : `leave out ${value}`}
      className="ml-1 shrink-0 rounded px-1 text-xs text-zinc-400 opacity-0 hover:bg-red-100 hover:text-red-700 focus:opacity-100 group-hover:opacity-100 dark:hover:bg-red-900/40"
    >
      {excluded ? "+" : "−"}
    </button>
  );
}

/** One facet: values with counts, click to filter, "−" to leave a value out, show more/less. */
export function Facet({
  field,
  values,
  selected,
  excluded,
  expanded,
  limit,
  onMore,
  ...handlers
}: Handlers & {
  field: string;
  values: FacetValue[];
  selected: string[];
  excluded: string[];
  expanded: boolean;
  limit: number;
  onMore: (field: string, more: boolean) => void;
}) {
  const [showAll, setShowAll] = useState(expanded);
  const title = <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">{facetTitle(field)}</h3>;
  if (field.endsWith("_paths")) {
    const render = (nodes: TreeNode[], depth: number) =>
      nodes.map((n) => {
        const isOn = selected.includes(n.value);
        const isOff = excluded.includes(n.value);
        return (
          <li key={n.value}>
            <div className="group flex items-center">
              <button
                onClick={() => handlers.onToggle(field, n.value)}
                style={{ paddingLeft: `${depth * 0.75 + 0.25}rem` }}
                className={`${row} ${isOn ? on : ""} ${isOff ? off : ""}`}
              >
                <span className="truncate">{n.label}</span>
                <span className="ml-2 text-zinc-500">{n.count}</span>
              </button>
              <ExcludeButton field={field} value={n.value} excluded={isOff} onExclude={handlers.onExclude} />
            </div>
            {n.children.length > 0 && <ul>{render(n.children, depth + 1)}</ul>}
          </li>
        );
      });
    return (
      <div>
        {title}
        <ul className="space-y-0.5 text-sm">{render(buildTree(values), 0)}</ul>
      </div>
    );
  }
  const shown = showAll ? values : values.slice(0, FACET_COLLAPSED);
  const more = values.length > FACET_COLLAPSED || values.length >= limit;
  return (
    <div>
      {title}
      <ul className="space-y-0.5 text-sm">
        {shown.map((v) => {
          const isOn = selected.includes(v.value);
          const isOff = excluded.includes(v.value);
          return (
            <li key={v.value} className="group flex items-center">
              <button onClick={() => handlers.onToggle(field, v.value)} className={`${row} px-1 ${isOn ? on : ""} ${isOff ? off : ""}`}>
                <span className="truncate">{v.value}</span>
                <span className="ml-2 text-zinc-500">{v.count}</span>
              </button>
              <ExcludeButton field={field} value={v.value} excluded={isOff} onExclude={handlers.onExclude} />
            </li>
          );
        })}
      </ul>
      {more && (
        <button
          onClick={() => {
            const next = !showAll;
            setShowAll(next);
            // the API lists up to 20 values; ask for up to 100 once they are all shown
            onMore(field, next && values.length >= limit);
          }}
          className="mt-1 text-xs text-blue-700 hover:underline dark:text-blue-400"
        >
          {showAll ? "show less" : "show more"}
        </button>
      )}
    </div>
  );
}

/** The filters in force, each with a remove button, whether or not its facet still lists it. */
export function SelectedFilters({
  f,
  x,
  from,
  to,
  onRemove,
  onRemoveExcluded,
  onClearDates,
  onClearAll,
}: {
  f?: Filters;
  x?: Filters;
  from?: string;
  to?: string;
  onRemove: (field: string, value: string) => void;
  onRemoveExcluded: (field: string, value: string) => void;
  onClearDates: () => void;
  onClearAll: () => void;
}) {
  const included = Object.entries(f ?? {}).flatMap(([k, vs]) => vs.map((v) => [k, v] as const));
  const excluded = Object.entries(x ?? {}).flatMap(([k, vs]) => vs.map((v) => [k, v] as const));
  if (!included.length && !excluded.length && !from && !to) return null;
  const chip = "flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs";
  return (
    <div className="flex flex-wrap items-center gap-1" aria-label="Filters in force">
      {included.map(([k, v]) => (
        <span key={`f${k}${v}`} className={`${chip} border-blue-300 bg-blue-50 dark:border-blue-800 dark:bg-blue-950`}>
          <span className="text-zinc-500">{facetTitle(k)}:</span> {v}
          <button onClick={() => onRemove(k, v)} aria-label={`remove ${facetTitle(k)} ${v}`} className="text-zinc-400 hover:text-red-600">
            ×
          </button>
        </span>
      ))}
      {excluded.map(([k, v]) => (
        <span key={`x${k}${v}`} className={`${chip} border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40`}>
          <span className="text-zinc-500">not {facetTitle(k)}:</span> {v}
          <button onClick={() => onRemoveExcluded(k, v)} aria-label={`remove not ${facetTitle(k)} ${v}`} className="text-zinc-400 hover:text-red-600">
            ×
          </button>
        </span>
      ))}
      {(from || to) && (
        <span className={`${chip} border-blue-300 bg-blue-50 dark:border-blue-800 dark:bg-blue-950`}>
          <span className="text-zinc-500">Modified:</span> {rangeLabel(from, to)}
          <button onClick={onClearDates} aria-label="remove the date range" className="text-zinc-400 hover:text-red-600">
            ×
          </button>
        </span>
      )}
      <button onClick={onClearAll} className="text-xs text-blue-700 hover:underline dark:text-blue-400">
        clear all
      </button>
    </div>
  );
}

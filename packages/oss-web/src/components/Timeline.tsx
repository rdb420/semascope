import { lazy, Suspense } from "react";
import type { FacetValue } from "../api";

/** `onPick` gets the clicked bar's bucket (its start date); `gap` is the bar width (+1YEAR, +1MONTH, +1DAY). */
export type TimelineProps = { buckets: FacetValue[]; onPick?: (from: string) => void; gap?: string };

// echarts is a large library and only the date histogram uses it, so it loads in
// its own chunk the first time a timeline is shown; the placeholder keeps the
// layout from jumping while it arrives.
const TimelineChart = lazy(() => import("./TimelineChart"));

export function Timeline(props: TimelineProps) {
  return (
    <Suspense fallback={<div className="h-40 w-full" aria-busy="true" />}>
      <TimelineChart {...props} />
    </Suspense>
  );
}

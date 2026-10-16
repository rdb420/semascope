import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { TimelineProps } from "./Timeline";
import { bucketLabel } from "../searchState";

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

// The echarts implementation behind <Timeline>; loaded on demand (see Timeline.tsx).
export default function TimelineChart({ buckets, onPick, gap = "+1YEAR" }: TimelineProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.setOption({
      grid: { left: 36, right: 12, top: 8, bottom: 28 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: buckets.map((b) => bucketLabel(b.value, gap)), axisLabel: { fontSize: 10 } },
      yAxis: { type: "value", minInterval: 1, axisLabel: { fontSize: 10 } },
      series: [{ type: "bar", data: buckets.map((b) => b.count), itemStyle: { color: "#2563eb" } }],
    });
    chart.on("click", (p) => {
      const bucket = buckets[p.dataIndex]?.value;
      if (bucket) onPick?.(bucket);
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [buckets, onPick, gap]);
  return <div ref={ref} className="h-40 w-full" />;
}

import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { FacetValue } from "../api";

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

export function Timeline({ buckets, onPick }: { buckets: FacetValue[]; onPick?: (from: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.setOption({
      grid: { left: 36, right: 12, top: 8, bottom: 28 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: buckets.map((b) => b.value.slice(0, 7)), axisLabel: { fontSize: 10 } },
      yAxis: { type: "value", minInterval: 1, axisLabel: { fontSize: 10 } },
      series: [{ type: "bar", data: buckets.map((b) => b.count), itemStyle: { color: "#2563eb" } }],
    });
    chart.on("click", (p) => onPick?.(buckets[p.dataIndex]?.value));
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [buckets, onPick]);
  return <div ref={ref} className="h-40 w-full" />;
}

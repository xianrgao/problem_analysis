import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChartData } from '../../lib/types';

type Trace = Record<string, any>;
const SUPPORTED = new Set(['bar', 'scatter', 'pie']);
const TYPED: Record<string, any> = { f8: Float64Array, f4: Float32Array, i1: Int8Array, u1: Uint8Array, i2: Int16Array, u2: Uint16Array, i4: Int32Array, u4: Uint32Array };

// Plotly 6 serializes numpy arrays as base64 typed arrays ({dtype, bdata}).
function values(input: unknown): unknown[] {
  if (Array.isArray(input)) return input;
  const typed = input as { dtype?: string; bdata?: string } | undefined;
  if (typed?.bdata && typed.dtype && TYPED[typed.dtype]) { const binary = atob(typed.bdata); const bytes = new Uint8Array(binary.length); for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i); return Array.from(new TYPED[typed.dtype](bytes.buffer)); }
  return [];
}
const finite = (items: unknown[]) => items.map(Number).filter(Number.isFinite);
function quantile(sorted: number[], q: number) { const position = (sorted.length - 1) * q; const low = Math.floor(position); const high = Math.ceil(position); return sorted[low] + (sorted[high] - sorted[low]) * (position - low); }

// Box/violin rendered from bar (Q1—Q3), whisker error bars and outlier markers; statistics use every data point.
function boxFallback(trace: Trace): Trace[] {
  const horizontal = trace.orientation === 'h'; const data = values(horizontal ? trace.x : trace.y); const rawCategories = values(horizontal ? trace.y : trace.x);
  const groups = new Map<string, number[]>();
  data.forEach((value, index) => { const number = Number(value); if (!Number.isFinite(number)) return; const key = String(rawCategories[index] ?? trace.name ?? ''); groups.set(key, [...(groups.get(key) || []), number]); });
  const categories = [...groups.keys()]; const stats = categories.map(key => { const sorted = groups.get(key)!.sort((a, b) => a - b); const q1 = quantile(sorted, 0.25), median = quantile(sorted, 0.5), q3 = quantile(sorted, 0.75), fence = 1.5 * (q3 - q1); const inside = sorted.filter(v => v >= q1 - fence && v <= q3 + fence); return { q1, median, q3, low: inside[0], high: inside[inside.length - 1], outliers: sorted.filter(v => v < q1 - fence || v > q3 + fence), n: sorted.length }; });
  const color = trace.marker?.color || trace.line?.color; const group = trace.offsetgroup || trace.legendgroup || trace.name || 'box'; const name = trace.name || '分布';
  const axis = (cats: unknown[], nums: unknown[]) => horizontal ? { y: cats, x: nums, orientation: 'h' } : { x: cats, y: nums };
  const hover = stats.map(s => `n=${s.n}<br>Q1=${s.q1.toPrecision(4)}<br>中位数=${s.median.toPrecision(4)}<br>Q3=${s.q3.toPrecision(4)}`);
  const whiskers = { type: 'data', symmetric: false, array: stats.map(s => s.high - s.median), arrayminus: stats.map(s => s.median - s.low), color: color || '#596479', thickness: 1.2, width: 0 };
  return [
    { type: 'bar', ...axis(categories, stats.map(s => s.q3 - s.q1)), base: stats.map(s => s.q1), name, legendgroup: group, offsetgroup: group, marker: { color, opacity: 0.45, line: { color, width: 1.2 } }, hovertext: hover, hoverinfo: 'text' },
    { type: 'scatter', mode: 'markers', ...axis(categories, stats.map(s => s.median)), name: `${name} 中位数`, legendgroup: group, offsetgroup: group, showlegend: false, marker: { symbol: horizontal ? 'line-ns-open' : 'line-ew-open', size: 22, color: color || '#172033', line: { width: 2 } }, [horizontal ? 'error_x' : 'error_y']: whiskers, hovertext: hover, hoverinfo: 'text' },
    { type: 'scatter', mode: 'markers', ...axis(stats.flatMap((s, i) => s.outliers.map(() => categories[i])), stats.flatMap(s => s.outliers)), name: `${name} 离群点`, legendgroup: group, offsetgroup: group, showlegend: false, marker: { color, size: 5, opacity: 0.7 } },
  ];
}

function histogramFallback(trace: Trace): Trace[] {
  const horizontal = trace.orientation === 'h'; const raw = values(horizontal ? trace.y : trace.x); const numbers = finite(raw);
  const axis = (cats: unknown[], counts: number[]) => horizontal ? { y: cats, x: counts, orientation: 'h' } : { x: cats, y: counts };
  const common = { type: 'bar', name: trace.name, legendgroup: trace.legendgroup, marker: { color: trace.marker?.color, opacity: trace.marker?.opacity ?? trace.opacity ?? 0.75 } };
  if (numbers.length < raw.length / 2) { const counts = new Map<string, number>(); raw.forEach(value => counts.set(String(value), (counts.get(String(value)) || 0) + 1)); return [{ ...common, ...axis([...counts.keys()], [...counts.values()]) }]; }
  const min = Math.min(...numbers), max = Math.max(...numbers); const bins = Math.max(1, Math.min(60, trace.nbinsx || Math.ceil(Math.log2(numbers.length) + 1))); const width = (max - min) / bins || 1;
  const counts = new Array(bins).fill(0); numbers.forEach(value => { counts[Math.min(bins - 1, Math.floor((value - min) / width))] += 1; });
  return [{ ...common, ...axis(counts.map((_, i) => min + width * (i + 0.5)), counts), width: width * 0.98, hovertext: counts.map((count, i) => `${(min + width * i).toPrecision(4)} – ${(min + width * (i + 1)).toPrecision(4)}<br>数量=${count}`), hoverinfo: 'text' }];
}

export function prepareFigure(figure: ChartData): { data: Trace[]; layout: Record<string, unknown>; unsupported: string[] } {
  const traces = (figure.data || []) as Trace[]; const unsupported = new Set<string>(); const layout: Record<string, any> = { ...(figure.layout || {}) };
  const data = traces.flatMap(trace => { const type = trace.type || 'scatter'; if (SUPPORTED.has(type)) return [trace]; if (type === 'box' || type === 'violin') { layout.barmode = 'group'; layout.scattermode = 'group'; return boxFallback(trace); } if (type === 'histogram') { layout.barmode = traces.filter(t => t.type === 'histogram').length > 1 ? 'overlay' : layout.barmode; layout.bargap = 0; return histogramFallback(trace); } unsupported.add(type); return []; });
  return { data, layout, unsupported: [...unsupported] };
}

export function ChartView({ figure }: { figure: ChartData }) {
  const node = useRef<HTMLDivElement>(null); const [error, setError] = useState(''); const prepared = useMemo(() => prepareFigure(figure), [figure]);
  useEffect(() => { let disposed = false; let cleanup = () => {}; import('plotly.js-basic-dist-min').then(async ({ default: Plotly }) => { if (!node.current || disposed) return; const el = node.current; try { await Plotly.react(el, prepared.data, { ...prepared.layout, autosize: true, width: undefined, height: 360, paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff', colorway: ['#3569db', '#ec8463', '#36a6a0', '#877bc4', '#d0a048'], font: { family: 'Segoe UI, Microsoft YaHei, sans-serif', size: 11, color: '#596479' }, margin: { l: 55, r: 20, t: 46, b: 54 }, legend: { orientation: 'h', y: -0.2 } }, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d','select2d'], toImageButtonOptions: { format: 'png', filename: 'linea-analysis', scale: 2 } }); const observer = new ResizeObserver(() => Plotly.Plots.resize(el)); observer.observe(el); cleanup = () => { observer.disconnect(); Plotly.purge(el); }; if (disposed) cleanup(); } catch (failure) { if (!disposed) setError(String(failure)); } }).catch(failure => setError(String(failure))); return () => { disposed = true; cleanup(); }; }, [prepared]);
  return error ? <div className="inline-error">图表加载失败：{error}</div> : <>{prepared.unsupported.length > 0 && <div className="inline-error">工作台未内置 {prepared.unsupported.join('、')} 图层，完整图表可在导出的离线报告中查看。</div>}<div className="plotly-chart" ref={node}/></>;
}

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChartData } from '../../lib/types';

type Trace = Record<string, any>;
// Neutral fallback marks exceed 3:1 graphical contrast on both chart surfaces.
const DEFAULT_INK = '#7b8aa4';
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
  const whiskers = { type: 'data', symmetric: false, array: stats.map(s => s.high - s.median), arrayminus: stats.map(s => s.median - s.low), color: color || DEFAULT_INK, thickness: 1.2, width: 0 };
  return [
    { type: 'bar', ...axis(categories, stats.map(s => s.q3 - s.q1)), base: stats.map(s => s.q1), name, legendgroup: group, offsetgroup: group, marker: { color, opacity: 0.45, line: { color, width: 1.2 } }, hovertext: hover, hoverinfo: 'text' },
    { type: 'scatter', mode: 'markers', ...axis(categories, stats.map(s => s.median)), name: `${name} 中位数`, legendgroup: group, offsetgroup: group, showlegend: false, marker: { symbol: horizontal ? 'line-ns-open' : 'line-ew-open', size: 22, color: color || DEFAULT_INK, line: { width: 2 } }, [horizontal ? 'error_x' : 'error_y']: whiskers, hovertext: hover, hoverinfo: 'text' },
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

type ThemeStyles = Pick<CSSStyleDeclaration, 'getPropertyValue'>;

// Only presentation attributes: relayout must never overwrite ranges or trace visibility.
export function chartTheme(layout: Record<string, unknown>, styles: ThemeStyles): Record<string, unknown> {
  const token = (name: string, fallback: string) => styles.getPropertyValue(name).trim() || fallback;
  const text = token('--chart-text', '#556072');
  const background = token('--chart-bg', '#ffffff');
  const axis = token('--chart-axis', '#cdd3de');
  const theme: Record<string, unknown> = {
    paper_bgcolor: background,
    plot_bgcolor: background,
    colorway: ['#2f64e6', '#d87b50', '#25988b', '#8470c5', '#b78829'].map((fallback, index) => token(`--chart-${index + 1}`, fallback)),
    'font.family': token('--font', 'Segoe UI, Microsoft YaHei, sans-serif'),
    'font.color': text,
    'title.font.color': token('--text', '#121826'),
    'legend.font.color': text,
    'legend.title.font.color': text,
    'legend.bgcolor': 'rgba(0,0,0,0)',
    'hoverlabel.bgcolor': token('--panel-raised', background),
    'hoverlabel.bordercolor': axis,
    'hoverlabel.font.color': token('--text', '#121826'),
    'modebar.bgcolor': 'rgba(0,0,0,0)',
    'modebar.color': text,
    'modebar.activecolor': token('--accent-text', '#2558d8'),
  };
  for (const name of new Set(['xaxis', 'yaxis', ...Object.keys(layout).filter(key => /^[xy]axis\d*$/.test(key))])) {
    theme[`${name}.gridcolor`] = token('--chart-grid', '#edf0f4');
    theme[`${name}.linecolor`] = axis;
    theme[`${name}.zerolinecolor`] = axis;
    theme[`${name}.tickfont.color`] = text;
    theme[`${name}.title.font.color`] = text;
  }
  return theme;
}

export function chartLayout(layout: Record<string, unknown>, styles: ThemeStyles): Record<string, unknown> {
  const result: Record<string, any> = {
    ...layout, autosize: true, width: undefined, height: 360,
    font: { ...(layout.font as object), size: 11 },
    margin: { l: 55, r: 20, t: 46, b: 54 },
    legend: { ...(layout.legend as object), orientation: 'h', y: -0.2 },
  };
  // Plotly's white template has near-black error bars. Use a cross-theme neutral
  // for these defaults, without touching explicit trace colors or named templates.
  if (!layout.template || typeof layout.template === 'object') {
    const template = (layout.template || {}) as Record<string, any>;
    const data = { ...(template.data || {}) };
    for (const kind of ['bar', 'scatter']) {
      data[kind] = (data[kind] || [{}]).map((trace: Trace) => {
        const next: Trace = { type: kind, ...trace };
        for (const key of ['error_x', 'error_y']) {
          const error = trace[key] || {};
          next[key] = { ...error, color: !error.color || error.color === '#2a3f5f' ? DEFAULT_INK : error.color };
        }
        return next;
      });
    }
    result.template = { ...template, data };
  }
  // Expand the same dotted updates for the initial render, cloning each branch so
  // stored figures, axis titles (including legacy strings), and data stay intact.
  for (const [path, value] of Object.entries(chartTheme(layout, styles))) {
    const keys = path.split('.');
    let target = result;
    for (const key of keys.slice(0, -1)) {
      const previous = target[key];
      target[key] = typeof previous === 'string' && key === 'title' ? { text: previous } : { ...previous };
      target = target[key];
    }
    target[keys[keys.length - 1]] = value;
  }
  return result;
}

export function ChartView({ figure }: { figure: ChartData }) {
  const node = useRef<HTMLDivElement>(null); const [error, setError] = useState(''); const prepared = useMemo(() => prepareFigure(figure), [figure]);
  useEffect(() => {
    let disposed = false;
    let cleanup = () => {};
    const report = (failure: unknown) => {
      if (!disposed) { disposed = true; cleanup(); setError(String(failure)); }
    };
    import('plotly.js-basic-dist-min').then(async ({ default: Plotly }) => {
      if (!node.current || disposed) return;
      const el = node.current;
      const styles = () => getComputedStyle(document.documentElement);
      try {
        await Plotly.react(el, prepared.data, chartLayout(prepared.layout, styles()), {
          responsive: true, displaylogo: false, modeBarButtonsToRemove: ['lasso2d', 'select2d'],
          toImageButtonOptions: { format: 'png', filename: 'linea-analysis', scale: 2 },
        });
      } catch (failure) { Plotly.purge(el); throw failure; }
      if (disposed) { Plotly.purge(el); return; }
      // Keep zoom, pan, selections and hidden traces when changing appearance.
      // Serialize rapid toggles so an older render cannot win over the latest one.
      let update = Promise.resolve();
      const updateTheme = () => {
        update = update.then(() => { if (!disposed) return Plotly.relayout(el, chartTheme(prepared.layout, styles())); }).catch(report);
      };
      const observer = new ResizeObserver(() => Plotly.Plots.resize(el));
      const themeObserver = new MutationObserver(updateTheme);
      observer.observe(el);
      themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
      cleanup = () => { observer.disconnect(); themeObserver.disconnect(); Plotly.purge(el); cleanup = () => {}; };
      updateTheme(); // Also catch a theme change while Plotly was loading.
    }).catch(report);
    return () => { disposed = true; cleanup(); };
  }, [prepared]);
  return error ? <div className="inline-error">图表加载失败：{error}</div> : <>{prepared.unsupported.length > 0 && <div className="inline-error">工作台未内置 {prepared.unsupported.join('、')} 图层，完整图表可在导出的离线报告中查看。</div>}<div className="plotly-chart" ref={node}/></>;
}

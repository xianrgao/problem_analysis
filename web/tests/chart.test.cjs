const test = require('node:test');
const assert = require('node:assert/strict');
const { loadTS, plain, styles, tokens, hooks } = require('./helpers.cjs');

const chart = loadTS('features/artifacts/ChartView.tsx');
const tick = () => new Promise(resolve => setImmediate(resolve));
function freeze(value) { if (value && typeof value === 'object') { Object.freeze(value); Object.values(value).forEach(freeze); } return value; }

for (const theme of ['light', 'dark']) {
  test(`${theme} chart layout preserves titles, ranges, ordering, axes and source figure`, () => {
    const original = freeze({
      title: '压装力趋势',
      xaxis: { title: '发生时间', range: [2, 8], autorange: false, categoryorder: 'array', categoryarray: ['OP20', 'OP10'] },
      yaxis: { title: { text: '力 / kN', font: { size: 14 } }, type: 'log', showgrid: false },
      yaxis2: { title: '副轴', overlaying: 'y', side: 'right', range: [0, 100] },
      font: { size: 16 }, legend: { title: { text: '班次' }, traceorder: 'reversed' },
      template: { layout: { xaxis: { gridcolor: '#ffffff' } } },
      barmode: 'overlay', uirevision: 'existing-revision',
    });
    const before = JSON.stringify(original);
    const layout = chart.chartLayout(original, styles(theme));
    assert.equal(JSON.stringify(original), before, 'the stored figure must not be mutated');
    assert.equal(layout.title.text, original.title);
    assert.equal(layout.xaxis.title.text, original.xaxis.title);
    assert.equal(layout.yaxis.title.text, '力 / kN');
    assert.equal(layout.yaxis.title.font.size, 14);
    assert.deepEqual(plain(layout.xaxis.range), [2, 8]);
    assert.equal(layout.xaxis.autorange, false);
    assert.deepEqual(plain(layout.xaxis.categoryarray), ['OP20', 'OP10']);
    assert.equal(layout.yaxis.type, 'log');
    assert.equal(layout.yaxis.showgrid, false);
    assert.equal(layout.yaxis2.overlaying, 'y');
    assert.deepEqual(plain(layout.yaxis2.range), [0, 100]);
    assert.equal(layout.legend.title.text, '班次');
    assert.equal(layout.legend.traceorder, 'reversed');
    assert.equal(layout.barmode, 'overlay');
    assert.equal(layout.uirevision, 'existing-revision');
    assert.equal(layout.paper_bgcolor, tokens(theme)['--chart-bg']);
    assert.equal(layout.plot_bgcolor, tokens(theme)['--chart-bg']);
    assert.equal(layout.yaxis2.gridcolor, tokens(theme)['--chart-grid']);
    assert.equal(layout.hoverlabel.font.color, tokens(theme)['--text']);
    assert.equal(layout.modebar.activecolor, tokens(theme)['--accent-text']);
    assert.equal(layout.height, 360);
  });
}

test('live theme updates contain no zoom, pan, selection or trace-visibility fields', () => {
  const update = chart.chartTheme({ xaxis2: {}, yaxis3: {} }, styles('dark'));
  assert.equal(update['xaxis2.gridcolor'], tokens('dark')['--chart-grid']);
  assert.equal(update['yaxis3.title.font.color'], tokens('dark')['--chart-text']);
  for (const key of Object.keys(update)) assert.doesNotMatch(key, /range|autorange|visible|selected|revision|data|width|height/);
  assert.ok(Object.values(update).every(value => Array.isArray(value) || (typeof value === 'string' && value.length)));
});

test('layout helpers have usable fallbacks and preserve object-form titles', () => {
  const layout = chart.chartLayout({ title: { text: 'Title', x: .1, font: { size: 18 } } }, { getPropertyValue: () => '' });
  assert.equal(layout.title.text, 'Title');
  assert.equal(layout.title.x, .1);
  assert.equal(layout.title.font.size, 18);
  assert.equal(layout.paper_bgcolor, '#ffffff');
  assert.ok(layout.font.family.length > 0);
});

test('supported chart traces, explicit series colors and hidden legend entries are untouched', () => {
  const figure = freeze({ data: [
    { type: 'bar', x: ['A', 'B'], y: [3, 9], marker: { color: '#dd7700' }, visible: 'legendonly' },
    { type: 'scatter', mode: 'lines', x: [0, 1], y: [2, 4], line: { color: '#3377aa' } },
    { type: 'pie', labels: ['OK', 'NC'], values: [80, 20] },
  ], layout: { title: 'Existing data' } });
  const prepared = chart.prepareFigure(figure);
  assert.deepEqual(plain(prepared.data), plain(figure.data));
  prepared.data.forEach((trace, index) => assert.equal(trace, figure.data[index]));
  assert.deepEqual(plain(prepared.unsupported), []);
});

test('box/violin fallback still computes all-point quantiles, whiskers and outliers', () => {
  for (const type of ['box', 'violin']) {
    const prepared = chart.prepareFigure({ data: [{ type, x: ['A', 'A', 'A', 'A', 'A'], y: [1, 2, 3, 4, 100], marker: { color: '#3377aa' } }] });
    assert.equal(prepared.data.length, 3);
    assert.deepEqual(plain(prepared.data[0].base), [2]);
    assert.deepEqual(plain(prepared.data[0].y), [2]);
    assert.deepEqual(plain(prepared.data[1].y), [3]);
    assert.deepEqual(plain(prepared.data[1].error_y.array), [1]);
    assert.deepEqual(plain(prepared.data[1].error_y.arrayminus), [2]);
    assert.deepEqual(plain(prepared.data[2].y), [100]);
    assert.equal(prepared.data[0].marker.color, '#3377aa');
    assert.equal(prepared.layout.barmode, 'group');
  }
});

test('default box marks and error bars remain visible without replacing explicit colors', () => {
  const prepared = chart.prepareFigure({ data: [{ type: 'box', y: [1, 2, 3, 4, 100] }] });
  assert.equal(prepared.data[1].marker.color, '#7b8aa4');
  assert.equal(prepared.data[1].error_y.color, '#7b8aa4');
  const source = freeze({ template: { layout: { showlegend: false }, data: { bar: [
    { type: 'bar', marker: { line: { width: .5 } }, error_y: { color: '#2a3f5f', thickness: 2 } },
    { type: 'bar', error_y: { color: '#dd7700' } },
  ] } } });
  const layout = chart.chartLayout(source, styles('dark'));
  assert.equal(layout.template.layout.showlegend, false);
  assert.equal(layout.template.data.bar[0].error_y.color, '#7b8aa4');
  assert.equal(layout.template.data.bar[0].error_y.thickness, 2);
  assert.equal(layout.template.data.bar[0].marker.line.width, .5);
  assert.equal(layout.template.data.bar[1].error_y.color, '#dd7700');
  assert.equal(source.template.data.bar[0].error_y.color, '#2a3f5f');
  assert.equal(chart.chartLayout({}, styles('dark')).template.data.scatter[0].type, 'scatter');
  assert.equal(chart.chartLayout({ template: 'custom-template' }, styles('dark')).template, 'custom-template');
});

test('histogram and typed-array inputs retain the existing conversions', () => {
  const array = new Float64Array([0, 1, 2, 3]);
  const figure = { data: [{ type: 'histogram', nbinsx: 2, x: { dtype: 'f8', bdata: Buffer.from(array.buffer).toString('base64') } }] };
  const prepared = chart.prepareFigure(figure);
  assert.deepEqual(plain(prepared.data[0].y), [2, 2]);
  assert.deepEqual(plain(prepared.data[0].x), [.75, 2.25]);
  assert.equal(prepared.layout.bargap, 0);
  const unsupported = chart.prepareFigure({ data: [{ type: 'heatmap' }] });
  assert.deepEqual(plain(unsupported.unsupported), ['heatmap']);
  assert.deepEqual(plain(unsupported.data), []);
});

function mountChart(options = {}) {
  const lifecycle = hooks();
  const element = {};
  const root = { dataset: { theme: 'light' } };
  const calls = { react: [], relayout: [], purge: [], resize: [] };
  const observers = { theme: [], resize: [] };
  function observer(kind) {
    return class {
      constructor(callback) { this.callback = callback; this.connected = false; observers[kind].push(this); }
      observe(target, config) { this.target = target; this.config = config; this.connected = true; }
      disconnect() { this.connected = false; }
    };
  }
  const Plotly = {
    async react(...args) { calls.react.push(args); await options.react?.(...args); },
    async relayout(...args) { calls.relayout.push(args); await options.relayout?.(...args); },
    purge(el) { calls.purge.push(el); },
    Plots: { resize(el) { calls.resize.push(el); } },
  };
  const { ChartView } = loadTS('features/artifacts/ChartView.tsx', {
    mocks: { react: lifecycle.react, 'plotly.js-basic-dist-min': Plotly },
    globals: { document: { documentElement: root }, getComputedStyle: () => styles(root.dataset.theme), ResizeObserver: observer('resize'), MutationObserver: observer('theme') },
  });
  const figure = { data: [{ type: 'scatter', x: [1, 2], y: [3, 4], visible: 'legendonly' }], layout: { xaxis: { range: [1, 1.5] } } };
  const render = () => lifecycle.render(() => ChartView({ figure }), refs => { refs[0].current = element; });
  render();
  return {
    ...lifecycle, calls, observers, figure, render,
    change(theme) { root.dataset.theme = theme; for (const item of observers.theme) if (item.connected) item.callback([]); },
  };
}

test('theme changes relayout an existing chart; resize/export configuration and cleanup remain intact', async () => {
  const view = mountChart();
  await tick();
  assert.equal(view.calls.react.length, 1);
  assert.equal(view.observers.theme.length, 1);
  assert.deepEqual(plain(view.observers.theme[0].config), { attributes: true, attributeFilter: ['data-theme'] });
  view.change('dark');
  await tick();
  view.render();
  assert.equal(view.calls.react.length, 1, 'no newPlot/react/purge on a theme change');
  assert.equal(view.calls.purge.length, 0);
  assert.equal(view.calls.relayout.at(-1)[1].paper_bgcolor, tokens('dark')['--chart-bg']);
  assert.equal(view.calls.react[0][1][0].visible, 'legendonly');
  assert.deepEqual(plain(view.calls.react[0][2].xaxis.range), [1, 1.5]);
  assert.deepEqual(plain(view.calls.react[0][3].toImageButtonOptions), { format: 'png', filename: 'linea-analysis', scale: 2 });
  view.observers.resize[0].callback();
  assert.equal(view.calls.resize.length, 1);
  view.unmount();
  assert.equal(view.calls.purge.length, 1);
  assert.ok([...view.observers.theme, ...view.observers.resize].every(item => !item.connected));
  const updates = view.calls.relayout.length;
  view.change('light');
  await tick();
  assert.equal(view.calls.relayout.length, updates);
});

test('rapid theme toggles are serialized and the last selected theme wins', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  let active = 0;
  let maxActive = 0;
  let count = 0;
  const view = mountChart({ relayout: async () => {
    maxActive = Math.max(maxActive, ++active);
    if (++count === 1) await pending;
    active--;
  } });
  await tick();
  view.change('dark'); view.change('light'); view.change('dark');
  await tick();
  assert.equal(view.calls.relayout.length, 1);
  release();
  await tick();
  assert.equal(maxActive, 1);
  assert.equal(view.calls.relayout.at(-1)[1].paper_bgcolor, tokens('dark')['--chart-bg']);
  assert.equal(view.calls.react.length, 1);
  view.unmount();
});

test('unmount before import completion does not create a chart or observers', async () => {
  const view = mountChart();
  view.unmount();
  await tick();
  assert.equal(view.calls.react.length, 0);
  assert.equal(view.observers.theme.length, 0);
  assert.deepEqual(view.states(), ['']);
});

test('unmount during the initial render purges once and never installs observers', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const view = mountChart({ react: () => pending });
  await tick();
  assert.equal(view.calls.react.length, 1);
  view.unmount();
  release();
  await tick();
  assert.equal(view.calls.purge.length, 1);
  assert.equal(view.observers.theme.length, 0);
  assert.deepEqual(view.states(), ['']);
});

test('render and relayout failures display errors and clean up their resources', async () => {
  for (const phase of ['react', 'relayout']) {
    const view = mountChart({ [phase]: async () => { throw new Error('Simulated chart failure'); } });
    await tick();
    assert.match(view.states()[0], /Simulated chart failure/);
    assert.equal(view.calls.purge.length, 1);
    assert.ok([...view.observers.theme, ...view.observers.resize].every(item => !item.connected));
    view.unmount();
    assert.equal(view.calls.purge.length, 1);
  }
});

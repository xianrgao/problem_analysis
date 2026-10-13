const test = require('node:test');
const assert = require('node:assert/strict');
const { read, tokens } = require('./helpers.cjs');

function luminance(hex) {
  const channels = hex.slice(1).match(/../g).map(part => parseInt(part, 16) / 255).map(channel => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4);
  return channels.reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
}
function contrast(a, b) {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + .05) / (dark + .05);
}

for (const theme of ['light', 'dark']) {
  test(`${theme} text and status palettes meet WCAG AA normal-text contrast`, () => {
    const colors = tokens(theme);
    const pairs = ['--text', '--text-2', '--text-3', '--text-4'].flatMap(text =>
      ['--bg', '--bg-subtle', '--sidebar-bg', '--panel', '--panel-muted', '--panel-raised'].map(surface => [text, surface]));
    pairs.push(...['--panel', '--panel-raised', '--accent-soft', '--accent-soft-hover'].map(surface => ['--accent-text', surface]));
    pairs.push(['--on-accent', '--accent'], ['--on-accent', '--accent-strong'],
      ['--ok-text', '--ok-soft'], ['--warn-text', '--warn-soft'], ['--danger-text', '--danger-soft'],
      ['--bubble-text', '--bubble-bg'], ['--bubble-link', '--bubble-bg'], ['--inverse-text', '--inverse-bg'],
      ['--text-3', '--accent-soft'], ['--chart-text', '--chart-bg']);
    for (const [text, surface] of pairs) {
      const ratio = contrast(colors[text], colors[surface]);
      assert.ok(ratio >= 4.5, `${theme}: ${text} on ${surface} = ${ratio.toFixed(2)}:1`);
    }
  });
}

test('fallback chart marks meet graphical contrast in both themes', () => {
  for (const theme of ['light', 'dark']) assert.ok(contrast('#7b8aa4', tokens(theme)['--chart-bg']) >= 3);
});

test('all component colors use defined tokens; no external font or stylesheet requests', () => {
  const defined = tokens();
  const files = ['tokens', 'layout', 'workbench', 'panels'].map(name => read(`src/styles/${name}.css`));
  for (const css of files) {
    for (const [, name] of css.matchAll(/var\((--[\w-]+)/g)) assert.ok(Object.hasOwn(defined, name), `undefined token ${name}`);
    assert.doesNotMatch(css, /@import|https?:\/\/|@font-face/);
  }
  for (const css of files.slice(1)) assert.doesNotMatch(css, /#[\da-f]{3,8}\b|rgba?\(/i, 'component colors must adapt to both themes');
});

test('virtual row geometry matches the 28px scrolling calculation', () => {
  const table = read('src/features/artifacts/DataTable.tsx');
  const css = read('src/styles/panels.css');
  const row = Number(table.match(/const ROW = (\d+)/)[1]);
  const [, height, lineHeight] = css.match(/\.table-scroll\.virtual tbody tr:not\(\.virtual-spacer\) td \{ height: (\d+)px; line-height: (\d+)px;/);
  assert.equal(Number(height), row);
  assert.equal(Number(lineHeight) + 5 * 2 + 1, row, 'line-height + vertical padding + border');
  assert.match(css, /\.table-scroll\.virtual tr\.virtual-spacer td \{ padding: 0; border: 0; background: transparent; \}/);
  assert.match(css, /\.table-scroll th\.row-index \{ z-index: 2; \}/);
});

test('preserves panel widths, collapsed navigation, and narrow-screen overlay layout', () => {
  const css = read('src/styles/layout.css');
  assert.equal(tokens()['--sidebar'], '264px');
  assert.equal(tokens()['--sidebar-collapsed'], '64px');
  assert.match(css, /\.panel-resizer \{[^}]*width: 5px/);
  assert.match(css, /\.panel-container \{[^}]*min-width: 300px/);
  assert.match(css, /@media \(max-width: 1100px\)[\s\S]*\.panel-container \{ position: absolute;/);
  assert.match(css, /\.sidebar\.collapsed \{ width: var\(--sidebar-collapsed\)/);
  assert.match(read('src/styles/workbench.css'), /\.suggestion-grid \{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
});

test('reduced-motion and keyboard-focus styles cover interactive UI', () => {
  const css = read('src/styles/tokens.css');
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /animation-iteration-count: 1 !important/);
  assert.match(css, /transition-duration: \.01ms !important/);
  assert.match(css, /:focus-visible \{ outline: 2px solid var\(--accent-text\)/);
  assert.match(read('src/styles/layout.css'), /\.session-item:focus-within \.session-menu-button/);
  assert.match(read('src/styles/panels.css'), /\.table-toolbar label:focus-within/);
});

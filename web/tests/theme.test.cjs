const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { read, loadTS, themeEnvironment, hooks, tokens } = require('./helpers.cjs');

function bootstrap(env) {
  const html = read('index.html');
  vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], env.globals);
  return env.document.documentElement.dataset.theme;
}
function mount(env) {
  const lifecycle = hooks();
  const { useTheme } = loadTS('app/useTheme.ts', { globals: env.globals, mocks: { react: lifecycle.react } });
  return { ...lifecycle, current: () => lifecycle.render(useTheme) };
}

for (const [saved, dark, expected] of [
  [null, false, 'light'], [null, true, 'dark'], ['invalid', true, 'dark'],
  ['light', true, 'light'], ['dark', false, 'dark'],
]) {
  test(`pre-paint bootstrap and hook agree: saved=${saved}, systemDark=${dark}`, () => {
    const env = themeEnvironment(saved, dark);
    assert.equal(bootstrap(env), expected);
    assert.equal(env.meta.content, tokens(expected)['--bg']);
    const hook = mount(env);
    assert.equal(hook.current().theme, expected);
    assert.equal(env.document.documentElement.dataset.theme, expected);
    assert.deepEqual(env.writes, [], 'initializing must not save a system-derived preference');
    hook.unmount();
  });
}

test('theme initializes even when localStorage is unavailable', () => {
  const env = themeEnvironment(null, true);
  env.storage.denied = true;
  assert.equal(bootstrap(env), 'dark');
  const hook = mount(env);
  assert.equal(hook.current().theme, 'dark');
  hook.current().toggleTheme();
  assert.equal(hook.current().theme, 'light');
  env.media.matches = true;
  env.media.emit('change');
  assert.equal(hook.current().theme, 'light', 'the explicit choice survives failed persistence in this tab');
  hook.unmount();
});

test('follows live system changes until an explicit toggle, then persists and restores', () => {
  const env = themeEnvironment();
  env.values.set('linea.panelWidth', '440');
  env.values.set('linea.active', 'existing-session');
  const hook = mount(env);
  assert.equal(hook.current().theme, 'light');
  env.media.matches = true;
  env.media.emit('change');
  assert.equal(hook.current().theme, 'dark');
  assert.deepEqual(env.writes, []);
  hook.current().toggleTheme();
  assert.equal(hook.current().theme, 'light');
  assert.equal(env.meta.content, tokens('light')['--bg']);
  env.media.matches = false;
  env.media.emit('change');
  env.media.matches = true;
  env.media.emit('change');
  assert.equal(hook.current().theme, 'light');
  assert.deepEqual(env.writes, [['linea.theme', 'light']]);
  assert.equal(env.values.get('linea.panelWidth'), '440');
  assert.equal(env.values.get('linea.active'), 'existing-session');
  hook.unmount();
  const restored = mount(env);
  assert.equal(restored.current().theme, 'light');
  restored.unmount();
});

test('cross-tab preferences synchronize and clearing the preference resumes system mode', () => {
  const env = themeEnvironment('light', true);
  const hook = mount(env);
  hook.current();
  env.values.set('linea.theme', 'dark');
  env.window.emit('storage', { key: 'linea.theme' });
  assert.equal(hook.current().theme, 'dark');
  env.values.set('linea.theme', 'light');
  env.window.emit('storage', { key: 'unrelated' });
  assert.equal(hook.current().theme, 'dark');
  env.window.emit('storage', { key: 'linea.theme' });
  assert.equal(hook.current().theme, 'light');
  env.values.delete('linea.theme');
  env.window.emit('storage', { key: null });
  assert.equal(hook.current().theme, 'dark');
  env.media.matches = false;
  env.media.emit('change');
  assert.equal(hook.current().theme, 'light');
  assert.deepEqual(env.writes, [], 'cross-tab updates must not write back in a loop');
  hook.unmount();
});

test('theme listeners are registered once and removed on unmount', () => {
  const env = themeEnvironment();
  const hook = mount(env);
  hook.current();
  hook.current().toggleTheme();
  hook.current();
  assert.equal(env.media.count('change'), 1);
  assert.equal(env.window.count('storage'), 1);
  hook.unmount();
  assert.equal(env.media.count('change'), 0);
  assert.equal(env.window.count('storage'), 0);
});

test('bootstrap executes before React and supplies both pre-paint backgrounds', () => {
  const html = read('index.html');
  assert.ok(html.indexOf('document.documentElement.dataset.theme') < html.indexOf('type="module"'));
  for (const theme of ['light', 'dark']) assert.ok(html.includes(tokens(theme)['--bg']));
  assert.match(read('src/app/App.tsx'), /aria-label="深色界面" aria-pressed=\{theme === 'dark'\}/);
});

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const ts = require('typescript');

const webRoot = path.resolve(__dirname, '..');
const read = relative => fs.readFileSync(path.join(webRoot, relative), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));

// Exercise TypeScript helpers with Node's built-in test runner; no browser or
// additional test dependencies. React effects/Plotly are mocked in unit tests.
function loadTS(relative, { globals = {}, mocks = {} } = {}) {
  const filename = path.join(webRoot, 'src', relative);
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    fileName: filename,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  });
  const module = { exports: {} };
  const nativeRequire = createRequire(filename);
  vm.runInNewContext(outputText, {
    exports: module.exports, module, atob,
    require: name => Object.hasOwn(mocks, name) ? mocks[name] : nativeRequire(name),
    ...globals,
  }, { filename });
  return module.exports;
}

function eventSource() {
  const listeners = new Map();
  return {
    addEventListener(name, listener) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(listener); },
    removeEventListener(name, listener) { listeners.get(name)?.delete(listener); },
    emit(name, event = {}) { for (const listener of [...(listeners.get(name) || [])]) listener(event); },
    count(name) { return listeners.get(name)?.size || 0; },
  };
}

function themeEnvironment(saved = null, dark = false) {
  const values = new Map(saved === null ? [] : [['linea.theme', saved]]);
  const writes = [];
  const storage = {
    denied: false,
    getItem(key) { if (this.denied) throw new Error('Storage unavailable'); return values.get(key) ?? null; },
    setItem(key, value) { if (this.denied) throw new Error('Storage unavailable'); writes.push([key, value]); values.set(key, value); },
  };
  const media = { ...eventSource(), matches: dark };
  const window = { ...eventSource(), matchMedia: () => media };
  const meta = { content: '', setAttribute(name, value) { this[name] = value; } };
  const document = { documentElement: { dataset: {} }, querySelector: () => meta };
  return { globals: { localStorage: storage, window, document }, storage, values, writes, media, window, document, meta };
}

// Minimal hook lifecycle harness: stable refs/memos, dependency-sensitive effects,
// explicit re-rendering, and cleanup. It does not claim to be a DOM/browser test.
function hooks() {
  const slots = [];
  let cursor = 0;
  let dirty = false;
  let pending = [];
  const changed = (a, b) => !a || !b || a.length !== b.length || a.some((value, index) => !Object.is(value, b[index]));
  const react = {
    useRef(initial) {
      const index = cursor++;
      return (slots[index] ??= { kind: 'ref', value: { current: initial } }).value;
    },
    useState(initial) {
      const index = cursor++;
      const slot = slots[index] ??= { kind: 'state', value: typeof initial === 'function' ? initial() : initial };
      return [slot.value, next => {
        const value = typeof next === 'function' ? next(slot.value) : next;
        if (!Object.is(value, slot.value)) { slot.value = value; dirty = true; }
      }];
    },
    useMemo(factory, deps) {
      const index = cursor++;
      if (!slots[index] || changed(slots[index].deps, deps)) slots[index] = { kind: 'memo', deps, value: factory() };
      return slots[index].value;
    },
    useEffect(callback, deps) {
      const index = cursor++;
      const slot = slots[index] ??= { kind: 'effect' };
      if (changed(slot.deps, deps)) {
        slot.deps = deps;
        pending.push(() => { slot.cleanup?.(); slot.cleanup = callback(); });
      }
    },
  };
  return {
    react,
    render(callback, attachRefs = () => {}) {
      let value;
      let attempts = 0;
      do {
        if (++attempts > 10) throw new Error('Hook did not settle');
        dirty = false; cursor = 0; pending = [];
        value = callback();
        attachRefs(slots.filter(slot => slot.kind === 'ref').map(slot => slot.value));
        for (const effect of pending) effect();
      } while (dirty);
      return value;
    },
    states: () => slots.filter(slot => slot.kind === 'state').map(slot => slot.value),
    unmount() { for (const slot of slots) if (slot.kind === 'effect') { slot.cleanup?.(); slot.cleanup = undefined; } },
  };
}

function tokens(theme = 'light') {
  const css = read('src/styles/tokens.css');
  const parse = body => Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(([, key, value]) => [key, value.trim()]));
  const light = parse(css.match(/:root\s*\{([^}]+)\}/)[1]);
  const dark = parse(css.match(/:root\[data-theme="dark"\]\s*\{([^}]+)\}/)[1]);
  return theme === 'dark' ? { ...light, ...dark } : light;
}
const styles = theme => ({ getPropertyValue: name => tokens(theme)[name] || '' });

module.exports = { read, plain, loadTS, eventSource, themeEnvironment, hooks, tokens, styles };

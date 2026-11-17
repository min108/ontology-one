import assert from 'node:assert/strict';
import test from 'node:test';
import postcss from 'postcss';
import { legacyCssPlugin, assertScopedLegacyCss } from './legacy-css.mjs';

const transform = async (css, from = '/project/frontend/src/legacy/styles.css') =>
  (await postcss([legacyCssPlugin()]).process(css, { from })).css;

test('legacy selectors and roots are scoped, including nested media', async () => {
  const css = await transform(
    ':root{--ink:red}html body{margin:0}@media(print){body .card,.card>button{color:red}}',
  );
  assert.doesNotThrow(() => assertScopedLegacyCss(css));
  assert.match(css, /\.legacy-root\{--ink:red\}/);
  assert.doesNotMatch(css, /\.legacy-root html|\.legacy-root body/);
});
test('keyframe steps remain valid selectors', async () => {
  const css = await transform(
    '@keyframes spin{from{opacity:0}50%{opacity:.5}to{opacity:1}}.spin{animation:spin 1s}',
  );
  assert.match(css, /from\{opacity:0\}/);
  assert.doesNotMatch(css, /legacy-root (from|to|50%)/);
});
test('React Flow vendor rules share the legacy boundary', async () => {
  const css = await transform(
    '.react-flow{display:block}',
    '/project/frontend/node_modules/@xyflow/react/dist/style.css',
  );
  assert.match(css, /\.legacy-root \.react-flow/);
});
test('new design and unrelated vendor CSS remain untouched', async () => {
  for (const from of [
    '/project/frontend/src/design/global.css',
    '/project/frontend/node_modules/other/style.css',
  ]) {
    assert.equal(await transform('.new{display:block}', from), '.new{display:block}');
  }
});
test('unscoped selectors fail the independent output check', () => {
  assert.throws(() => assertScopedLegacyCss('.legacy-root .ok,body .leak{color:red}'));
  assert.throws(() => assertScopedLegacyCss('@media(print){.unscoped{display:none}}'));
  assert.throws(() => assertScopedLegacyCss('.legacy-root-bypass .leak{display:none}'));
});
test('already scoped selectors are not double-prefixed', async () => {
  const css = await transform('.legacy-root .card{display:block}');
  assert.ok(css.startsWith('.legacy-root .card{display:block}'));
  assert.doesNotMatch(css, /\.legacy-root \.legacy-root/);
  assert.doesNotThrow(() => assertScopedLegacyCss(css));
});

test('the original universal reset still includes the container itself', async () => {
  const css = await transform('*{box-sizing:border-box}');
  assert.match(css, /\.legacy-root, \.legacy-root \*/);
});

test('legacy typography is clamped at build time without altering layout or new CSS', async () => {
  const css = await transform('.row{font-size:10px;grid-template-columns:1fr 2fr}.badge{font-size:9px}.heading{font-size:25px}.copy{font:500 11px/1.8 serif}.relative{font-size:.9em}');
  assert.match(css, /\.row\{font-size:var\(--zy-font-body\);grid-template-columns:1fr 2fr\}/);
  assert.match(css, /\.badge\{font-size:var\(--zy-font-caption\)\}/);
  assert.match(css, /\.heading\{font-size:var\(--zy-font-heading\)\}/);
  assert.match(css, /font:500 var\(--zy-font-body\)\/1.8/);
  assert.match(css, /\.relative\{font-size:var\(--zy-font-body\)\}/);
  assert.equal(await transform('.new{font-size:10px}', '/project/frontend/src/design/global.css'), '.new{font-size:10px}');
});
test('legacy surfaces lose gradients, translucency and serif titles while remaining scoped', async () => {
  const css = await transform('h1{font-family:Georgia,serif}.card{background:linear-gradient(red,blue);backdrop-filter:blur(10px);box-shadow:0 3px 5px #fff2}.panel{background:rgba(255,255,255,.5)}');
  assert.doesNotMatch(css, /linear-gradient|backdrop-filter:blur|font-family:Georgia/);
  assert.match(css, /background:var\(--zy-color-surface\)/);
  assert.doesNotThrow(() => assertScopedLegacyCss(css));
});

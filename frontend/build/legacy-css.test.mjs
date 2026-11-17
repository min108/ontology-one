import assert from 'node:assert/strict';
import test from 'node:test';
import postcss from 'postcss';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
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


test('generated legacy buttons retain readable solid colors after lazy page styles load', async () => {
  const tokens = readFileSync(new URL('../src/design/tokens.css', import.meta.url), 'utf8');
  let css = tokens;
  for (const path of ['styles.css', 'pages/experience.css', 'pages/assets.css']) {
    const source = readFileSync(new URL('../src/legacy/' + path, import.meta.url), 'utf8');
    css += await transform(source.replace(/@import[^;]+;/g, ''), '/project/frontend/src/legacy/' + path);
  }
  const variants = ['btn-primary', 'btn-ai', 'btn-ai-outline', 'btn-primary btn-ai'];
  const contexts = ['page-actions', 'exp-page', 'exp-form', 'asset-modal-footer'];
  const dom = new JSDOM(`<style>${css}</style><div class="legacy-root">${contexts.map(context =>
    `<section class="${context}">${variants.map(variant => `<button class="btn ${variant}">${context} ${variant}</button>`).join('')}</section>`
  ).join('')}</div>`);
  const luminance = color => color.match(/[\d.]+/g).slice(0, 3).map(Number)
    .map(channel => channel / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  try {
    for (const button of dom.window.document.querySelectorAll('button')) {
      const style = dom.window.getComputedStyle(button);
      const values = [luminance(style.color), luminance(style.backgroundColor)].sort((a, b) => b - a);
      const contrast = (values[0] + .05) / (values[1] + .05);
      assert.ok(contrast >= 4.5, `${button.textContent}: contrast ${contrast}, ${style.color} on ${style.backgroundColor}`);
      assert.equal(style.backgroundImage, 'none', `${button.textContent}: no gradient`);
    }
  } finally { dom.window.close(); }
});


test('only redundant top-level trails disappear after lazy detail CSS, retaining real returns and parents', async () => {
  let css = '';
  for (const path of ['styles.css', 'pages/materials.css', 'pages/datasets.css', 'navigation.css']) {
    const source = readFileSync(new URL('../src/legacy/' + path, import.meta.url), 'utf8');
    css += await transform(source.replace(/@import[^;]+;/g, ''), '/project/frontend/src/legacy/' + path);
  }
  const dom = new JSDOM(`<style>${css}</style><div class="legacy-root" data-legacy-page>
    <div class="page materials-detail-page"><a class="breadcrumb" href="/materials">Repeated trail</a>
      <header><div data-legacy-header-context><div class="context-trail"><a class="context-back" href="/work?via=source">Original source</a><details class="context-belonging"><summary>Business parents</summary></details></div><nav data-object-return>Fallback return</nav></div><h1>Material</h1></header>
    </div><div class="page"><a class="breadcrumb" href="/materials">Error return</a><p>Unavailable</p></div>
    <div class="page"><nav class="asset-detail-breadcrumb">Asset return</nav><nav class="sd-breadcrumb">Skill return</nav><nav class="pw-breadcrumb">Parent project</nav><h1>Detail</h1></div>
  </div><div class="page"><nav class="breadcrumb">New UI trail</nav><h1>New UI</h1></div>`);
  try {
    const document = dom.window.document;
    const display = selector => dom.window.getComputedStyle(document.querySelector(selector)).display;
    assert.equal(display('.materials-detail-page > .breadcrumb'), 'none');
    for (const selector of ['.page:not(.materials-detail-page) > .breadcrumb', '.context-trail', '.context-belonging', '[data-object-return]', '.asset-detail-breadcrumb', '.sd-breadcrumb', '.pw-breadcrumb', 'body > .page > .breadcrumb']) {
      assert.notEqual(display(selector), 'none', selector);
    }
    assert.equal(document.querySelector('.context-back').getAttribute('href'), '/work?via=source');
  } finally { dom.window.close(); }
});

test('legacy prose links have a non-color cue after page styles without changing action buttons', async () => {
  let css = '';
  for (const path of ['styles.css', 'pages/materials.css']) {
    const source = readFileSync(new URL('../src/legacy/' + path, import.meta.url), 'utf8');
    css += await transform(source.replace(/@import[^;]+;/g, ''), '/project/frontend/src/legacy/' + path);
  }
  const dom = new JSDOM(`<style>${css}</style><div class="legacy-root"><div class="materials-footnotes"><p>Continue in <a href="/datasets">Data intake</a>.</p></div><a class="btn" href="/datasets">Open</a></div>`);
  try {
    const link = dom.window.document.querySelector('p a');
    assert.equal(dom.window.getComputedStyle(link).textDecorationLine, 'underline');
    assert.equal(link.getAttribute('href'), '/datasets');
    assert.notEqual(dom.window.getComputedStyle(dom.window.document.querySelector('.btn')).textDecorationLine, 'underline');
  } finally { dom.window.close(); }
});

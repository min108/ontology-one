/*
 * 知元界面基准 v1 · 基准页自检脚本
 *
 * 用法（仓库根目录）：
 *   NODE_PATH=$(npm root -g) node docs/design/benchmarks/check-baseline.js
 * 需要 playwright（chromium）。不联网。有不通过项时退出码为 1。
 *
 * 检查项（口径与 UI-BASELINE.md §11 一致，实现后的页面按同一口径检查）：
 *   A1 没有小于 12px 的文字
 *   A2 小于 14px 的文字只出现在“次要”类里（标签、时间、标注等）
 *   A3 页面出现的字号不超过 5 种
 *   B1 页面标题左边线 x = 导航宽 + 页边距；正文首块与标题同一条左边线
 *   B2 内容区之上只有顶栏和标题区两条；1366×768 下内容区起点 y 不超过 196（工作台 256）
 *   B3 1366×768 下列表页第一行数据的 y 不超过 264
 *   C1 没有横向滚动，内容不超出视口
 *   D1 首屏字符数不超过预算：工作台 400、档案页 500、分栏页 650、列表页 800（专业视图各加 100）
 *   D2 工作台与档案页：全部层级不少于 600 字符时，首屏不超过全部层级的一半
 *   E1 一屏内强调色不超过 3 处（品牌标志与键盘焦点环不计）
 *   E2 一屏只有一个可用的主要按钮
 *   G1 简洁视图表格不超过 6 列；每行行内按钮不超过 2 个
 *   G2 状态标签都有图标和文字
 *   F1 文件里没有外部 URL；样式里没有颜色、字号的裸值
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const DIR = __dirname;
const PAGES = [
  { file: '01-workbench.html', form: 'work', states: ['default', 'empty'] },
  { file: '02-list-catalog.html', form: 'list', states: ['default', 'empty'], views: true },
  { file: '03-archive-element.html', form: 'archive', states: ['default', 'evidence'], views: true },
  { file: '04-review-changeset.html', form: 'split', states: ['default', 'edit', 'done'], views: true },
  { file: '05-reviews-center.html', form: 'split', states: ['default', 'return', 'blocked', 'empty'], views: true },
  { file: '06-shell-and-states.html', form: 'board', states: [''] },
];
/* 首屏字符预算（1440×900，简洁视图；专业视图各加 100）与内容区起点上限（1366×768） */
const BUDGET = { work: 400, archive: 500, split: 650, list: 800 };
const BODY_Y = { work: 256, archive: 196, split: 196, list: 196 };
const SIZES = [
  [1440, 900],
  [1366, 768],
  [1920, 1080],
];
/* 允许小于 14px 的“次要”类 */
const SMALL_OK =
  '.zy-caption,.zy-tag,.zy-label,.zy-kbd,.zy-count,.zy-pick__meta,.zy-pick__state,.zy-group,.zy-step__n,.zy-head__back,.zy-avatar,.zy-seg small,.zy-nav--collapsed,.zy-nav__item,.bd-cap,[data-meta]';

function staticChecks() {
  const out = [];
  const files = fs
    .readdirSync(DIR)
    .filter((f) => f.endsWith('.html'))
    .map((f) => path.join(DIR, f))
    .concat([path.join(DIR, 'assets/base.css'), path.join(DIR, 'assets/base.js'), path.join(DIR, '../tokens-v2.css')]);
  for (const f of files) {
    const text = fs.readFileSync(f, 'utf8');
    const name = path.relative(DIR, f);
    const urls = text.match(/(https?:)?\/\/[a-z0-9.-]+\.[a-z]{2,}/gi) || [];
    if (urls.length) out.push(`F1 ${name} 出现外部 URL：${urls.slice(0, 3).join(' ')}`);
    if (f.endsWith('tokens-v2.css') || f.endsWith('.js')) continue;
    /* 样式部分：.css 全文，.html 取 <style> */
    const css = f.endsWith('.css') ? text : (text.match(/<style>[\s\S]*?<\/style>/g) || []).join('\n');
    const body = css.replace(/\/\*[\s\S]*?\*\//g, '').replace(/@media[^{]+\{/g, '');
    const raw = body.match(/#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(|\b\d+(\.\d+)?(px|rem|em|pt)\b/gi) || [];
    if (raw.length) out.push(`F1 ${name} 样式里有裸值：${[...new Set(raw)].slice(0, 6).join(' ')}`);
  }
  return out;
}

(async () => {
  const problems = staticChecks();
  const rows = [];
  const browser = await chromium.launch();
  for (const [w, h] of SIZES) {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    for (const pg of PAGES) {
      const views = pg.views ? ['simple', 'pro'] : ['simple'];
      for (const state of pg.states)
        for (const view of views) {
          const hash = [state && 'state=' + state, 'view=' + view, 'bare'].filter(Boolean).join('&');
          await page.goto('about:blank');
          await page.goto('file://' + path.join(DIR, pg.file) + '#' + hash);
          await page.evaluate(() => document.fonts.ready);
          const r = await page.evaluate(
            ({ SMALL_OK, board }) => {
              const vw = innerWidth,
                vh = innerHeight;
              const main = document.querySelector('.zy-main');
              const shown = (el) => {
                if (!el.getClientRects().length) return false;
                const cs = getComputedStyle(el);
                return cs.visibility !== 'hidden' && cs.display !== 'none';
              };
              const sizes = new Map();
              let minSize = 99;
              const smallBad = [];
              let first = 0,
                all = 0;
              const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
              while (walker.nextNode()) {
                const node = walker.currentNode;
                const text = node.textContent.replace(/\s+/g, '');
                const el = node.parentElement;
                if (!text || !el || el.closest('script,style,svg,[data-meta]')) continue;
                const inMain = main.contains(el);
                const folded = el.closest('details:not([open])') && !el.closest('summary');
                const visible = shown(el) && !folded;
                /* 全部层级：当前状态下可见的，加上收起的折叠行和未打开的抽屉 */
                const layered = !visible && (el.closest('details:not([open])') || el.closest('.zy-drawer'));
                if (inMain && (visible || layered) && !el.closest('[hidden]:not(.zy-drawer)')) all += text.length;
                if (!visible) continue;
                const size = parseFloat(getComputedStyle(el).fontSize);
                sizes.set(size, (sizes.get(size) || 0) + 1);
                minSize = Math.min(minSize, size);
                if (size < 14 && !el.closest(SMALL_OK)) smallBad.push(text.slice(0, 12) + '(' + size + ')');
                if (inMain && !el.closest('.zy-drawer')) {
                  const range = document.createRange();
                  range.selectNodeContents(node);
                  const box = range.getBoundingClientRect();
                  if (box.top < vh && box.bottom > 0 && box.left < vw) first += text.length;
                }
              }
              const nav = document.querySelector('body > .zy-nav').getBoundingClientRect().width;
              const pad = parseFloat(getComputedStyle(document.body).getPropertyValue('--pad'));
              const title = [...document.querySelectorAll('.zy-head .zy-title')].find(shown);
              const body = [...document.querySelectorAll('.zy-body')].find(shown);
              const firstBlock = body && [...body.children].find(shown);
              const row = [...document.querySelectorAll('.zy-table tbody tr')].find(shown);
              /* 强调色出现处：按“块”计数 */
              const accent = getComputedStyle(document.documentElement).getPropertyValue('--zy-brand-500').trim();
              const probe = document.createElement('i');
              probe.style.color = accent;
              document.body.append(probe);
              const accentRgb = getComputedStyle(probe).color;
              probe.remove();
              let accents = 0;
              const scope = board ? [] : [...document.querySelectorAll('body *')];
              for (const el of scope) {
                if (!shown(el) || el.closest('[data-meta],.zy-brand')) continue;
                const box = el.getBoundingClientRect();
                if (box.bottom <= 0 || box.top >= vh) continue;
                const cs = getComputedStyle(el);
                const before = getComputedStyle(el, '::before');
                const hit =
                  cs.backgroundColor === accentRgb ||
                  (before.content !== 'none' && before.backgroundColor === accentRgb) ||
                  (el.matches('.zy-note--decide') && true);
                if (hit) accents++;
              }
              const inView = (el) => {
                const box = el.getBoundingClientRect();
                return shown(el) && box.bottom > 0 && box.top < vh;
              };
              const primaries = board ? 0 : [...document.querySelectorAll('.zy-btn--primary:not([disabled])')].filter(inView).length;
              const cols = Math.max(0, ...[...document.querySelectorAll('.zy-table thead tr')].filter(shown).map((tr) => [...tr.children].filter(shown).length));
              const rowBtns = Math.max(0, ...[...document.querySelectorAll('.zy-table__ops')].filter(shown).map((o) => o.querySelectorAll('.zy-btn').length));
              const bareTags = [...document.querySelectorAll('.zy-tag')].filter((t) => shown(t) && !(t.querySelector('svg') && t.textContent.trim())).length;
              return {
                primaries,
                cols,
                rowBtns,
                bareTags,
                minSize,
                sizes: [...sizes.keys()].sort((a, b) => a - b),
                smallBad: smallBad.slice(0, 5),
                titleX: title ? Math.round(title.getBoundingClientRect().left) : null,
                expectX: Math.round(nav + pad),
                blockX: firstBlock ? Math.round(firstBlock.getBoundingClientRect().left) : null,
                bodyY: body ? Math.round(body.getBoundingClientRect().top) : null,
                rowY: row ? Math.round(row.getBoundingClientRect().top) : null,
                overflowX: document.documentElement.scrollWidth > vw || main.scrollWidth > main.clientWidth + 1,
                first,
                all,
                accents,
              };
            },
            { SMALL_OK, board: pg.form === 'board' },
          );
          const id = `${pg.file} [${state || '-'}/${view}] ${w}×${h}`;
          const bad = (msg) => problems.push(`${msg}　${id}`);
          if (r.minSize < 12) bad(`A1 最小字号 ${r.minSize}`);
          if (r.smallBad.length) bad(`A2 正文类文字小于 14：${r.smallBad.join('、')}`);
          if (r.sizes.length > 5) bad(`A3 字号 ${r.sizes.length} 种：${r.sizes.join('/')}`);
          if (r.titleX !== r.expectX) bad(`B1 标题左边线 ${r.titleX}，应为 ${r.expectX}`);
          if (r.blockX !== null && r.blockX !== r.titleX) bad(`B1 正文首块左边线 ${r.blockX}，标题 ${r.titleX}`);
          const board = pg.form === 'board';
          if (w === 1366 && !board && r.bodyY > BODY_Y[pg.form]) bad(`B2 内容区起点 y=${r.bodyY}`);
          if (w === 1366 && pg.form === 'list' && r.rowY !== null && r.rowY > 264) bad(`B3 第一行数据 y=${r.rowY}`);
          if (r.overflowX) bad('C1 出现横向滚动');
          if (!board && w === 1440) {
            const cap = BUDGET[pg.form] + (view === 'pro' ? 100 : 0);
            if (r.first > cap) bad(`D1 首屏 ${r.first} 字符，预算 ${cap}`);
            if (['work', 'archive'].includes(pg.form) && r.all >= 600 && r.first / r.all > 0.5)
              bad(`D2 首屏占比 ${Math.round((r.first / r.all) * 100)}%`);
          }
          if (!board && r.accents > 3) bad(`E1 强调色 ${r.accents} 处`);
          if (r.primaries > 1) bad(`E2 主要按钮 ${r.primaries} 个`);
          if (view === 'simple' && r.cols > 6) bad(`G1 表格 ${r.cols} 列`);
          if (r.rowBtns > 2) bad(`G1 行内按钮 ${r.rowBtns} 个`);
          if (r.bareTags) bad(`G2 ${r.bareTags} 个标签缺图标或文字`);
          rows.push({ id, ...r });
        }
    }
    await page.close();
  }
  await browser.close();

  const pick = rows.filter((r) => r.id.includes('1440×900'));
  console.log('页面 [状态/视图] 视口 | 最小字号 | 字号种类 | 标题 x | 内容起点 y | 第一行 y | 首屏字符 / 全部层级 | 强调色处数');
  for (const r of pick)
    console.log(
      `${r.id} | ${r.minSize} | ${r.sizes.join('/')} | ${r.titleX} | ${r.bodyY} | ${r.rowY ?? '-'} | ${r.first} / ${r.all} | ${r.accents}`,
    );
  console.log('\n1366×768：');
  for (const r of rows.filter((x) => x.id.includes('1366×768') && x.id.includes('/simple')))
    console.log(`${r.id} | 标题 x ${r.titleX} | 内容起点 y ${r.bodyY} | 第一行 y ${r.rowY ?? '-'}`);
  console.log('\n1920×1080 标题 x：' + [...new Set(rows.filter((x) => x.id.includes('1920×1080')).map((x) => x.titleX))].join('、'));
  console.log(`\n共检查 ${rows.length} 个页面状态。` + (problems.length ? `不通过 ${problems.length} 项：` : '全部通过。'));
  for (const p of problems) console.log('  ✗ ' + p);
  process.exit(problems.length ? 1 : 0);
})();

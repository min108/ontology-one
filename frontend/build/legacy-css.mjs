import { readFileSync } from 'node:fs';
import postcss from 'postcss';
import prefixSelector from 'postcss-prefix-selector';

const files = [
  /[/\\]src[/\\]legacy[/\\]/,
  /[/\\]node_modules[/\\]@xyflow[/\\]react[/\\]dist[/\\]style\.css$/,
];
const scoped = (selector) => /^\.legacy-root(?=$|[\s.#:[>+~])/.test(selector);
const isFrame = (rule) => rule.parent?.type === 'atrule' && /^(?:-\w+-)?keyframes$/i.test(rule.parent.name);

export function assertScopedLegacyCss(css) {
  const root = typeof css === 'string' ? postcss.parse(css) : css;
  let count = 0;
  root.walkRules((rule) => {
    if (isFrame(rule)) return;
    for (const selector of rule.selectors) {
      if (!scoped(selector) || /(^|[\s>+~,(])(?:html|body|:root)(?=$|[\s>+~.#[:),])/.test(selector)) {
        throw rule.error(`Legacy selector escaped its container: ${selector}`);
      }
      count++;
    }
  });
  return count;
}


const bridge = readFileSync(new URL('../src/design/legacy-bridge.css', import.meta.url), 'utf8');
const secondary = /(?:badge|caption|eyebrow|kicker|(?:^|[.\s>])small|(?:^|[.\s>])time|timestamp|(?:^|[.\s>])tag)(?:[.\s>:[-]|$)/i;
function fontToken(size, selector) {
  const value = Number(size);
  const tier = value <= 13 && secondary.test(selector) ? 'caption' : value <= 15 ? 'body' : value <= 18 ? 'title' : value < 32 ? 'heading' : 'display';
  return `var(--zy-font-${tier})`;
}
function normalizeLegacy(root) {
  root.walkDecls((decl) => {
    const selector = decl.parent.selector || '';
    if (decl.prop === 'font-size') decl.value = decl.value.replace(/^(\d*\.?\d+)(px|em|rem)$/, (_, size, unit) => fontToken(Number(size) * (unit === 'em' ? 14 : unit === 'rem' ? 16 : 1), selector));
    if (decl.prop === 'font') decl.value = decl.value.replace(/(\d+(?:\.\d+)?)px(?=[\s/])/, (_, size) => fontToken(size, selector));
    if (decl.prop === 'font-family' && /(?:serif|Georgia|Songti|SimSun)/i.test(decl.value) && !/sans-serif/i.test(decl.value)) decl.value = 'var(--zy-font-family)';
    if (/^background(?:-image|-color)?$/.test(decl.prop) && /(?:gradient\(|rgba\(|#[\da-f]{8}\b|#[\da-f]{4}\b)/i.test(decl.value)) {
      decl.value = /(?:backdrop|mask|overlay)/.test(selector) ? 'var(--zy-overlay)' : 'var(--zy-color-surface)';
      if (decl.prop === 'background-image') decl.prop = 'background';
    }
    if (decl.prop === 'backdrop-filter' || decl.prop === '-webkit-backdrop-filter') decl.value = 'none';
    if (decl.prop === 'box-shadow') decl.value = 'var(--zy-shadow-none)';
  });
}

export function legacyCssPlugin() {
  const plugin = prefixSelector({
    prefix: '.legacy-root',
    includeFiles: files,
    transform(prefix, selector, prefixed) {
      if (scoped(selector)) return selector;
      if (/^\*(?:::(?:before|after))?$/.test(selector)) {
        return `${selector.replace('*', prefix)}, ${prefixed}`;
      }
      let rewritten = selector.replace(/(^|[\s>+~,(])(?:html|body|:root)(?=$|[\s>+~.#[:),])/g, `$1${prefix}`);
      while (/\.legacy-root(?:\s*>\s*|\s+)\.legacy-root/.test(rewritten)) {
        rewritten = rewritten.replace(/\.legacy-root(?:\s*>\s*|\s+)\.legacy-root/g, prefix);
      }
      return scoped(rewritten) ? rewritten : rewritten === selector ? prefixed : `${prefix} ${rewritten}`;
    },
  });
  const prepare = plugin.prepare;
  plugin.prepare = (result) => {
    const visitors = prepare(result);
    return visitors ? { ...visitors, OnceExit(root) {
      normalizeLegacy(root);
      // The shared stylesheet is loaded with every legacy surface; append the isolated bridge last.
      if (/[/\\]src[/\\]legacy[/\\]styles\.css$/.test(result.opts.from || '')) root.append(postcss.parse(bridge).nodes);
      assertScopedLegacyCss(root);
    } } : undefined;
  };
  return plugin;
}

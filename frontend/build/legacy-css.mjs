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
    return visitors ? { ...visitors, OnceExit: assertScopedLegacyCss } : undefined;
  };
  return plugin;
}

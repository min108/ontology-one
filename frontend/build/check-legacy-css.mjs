import { readFile, readdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { assertScopedLegacyCss, legacyCssPlugin } from './legacy-css.mjs';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const root = resolve(frontend, 'src/legacy');
const paths = (await readdir(root, { recursive: true }))
  .filter((path) => path.endsWith('.css'))
  .map((path) => resolve(root, path));
paths.push(resolve(frontend, 'node_modules/@xyflow/react/dist/style.css'));
let selectors = 0;
for (const from of paths) {
  const result = await postcss([legacyCssPlugin()]).process(await readFile(from, 'utf8'), { from });
  selectors += assertScopedLegacyCss(result.css);
}
if (!selectors || paths.length < 2) throw new Error('No legacy CSS was checked');
console.log(`Scoped legacy CSS: ${selectors} selectors across ${paths.length} stylesheets`);

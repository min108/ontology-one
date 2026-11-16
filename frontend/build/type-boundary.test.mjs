import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const compiler = resolve(frontend, 'node_modules/typescript/bin/tsc');
function compile(args) {
  return spawnSync(process.execPath, [compiler, ...args], {
    cwd: frontend,
    encoding: 'utf8',
    maxBuffer: 20 * 1024 * 1024,
  });
}

test('strict program consumes declarations without compiling legacy source', () => {
  const result = compile(['--project', 'tsconfig.json', '--listFilesOnly']);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.ok(result.stdout.includes('node_modules/.cache/legacy-types/legacy/'));
  assert.ok(
    !result.stdout
      .split('\n')
      .some((path) =>
        path.replaceAll('\\', '/').startsWith(resolve(frontend, 'src/legacy').replaceAll('\\', '/') + '/'),
      ),
  );
});

test('new consumers remain strict when they import a legacy declaration', () => {
  const scratch = resolve(frontend, '../work/r1-5/type-contract');
  mkdirSync(scratch, { recursive: true });
  const directory = mkdtempSync(resolve(scratch, 'probe-'));
  try {
    writeFileSync(
      resolve(directory, 'probe.ts'),
      "import App from '@legacy/App'; const value: number = null; void value; void App;\n",
    );
    writeFileSync(
      resolve(directory, 'tsconfig.json'),
      JSON.stringify({
        extends: resolve(frontend, 'tsconfig.json'),
        compilerOptions: { types: [] },
        include: [],
        files: ['./probe.ts'],
      }),
    );
    const result = compile(['--project', resolve(directory, 'tsconfig.json')]);
    assert.notEqual(result.status, 0);
    assert.match(result.stdout, /probe\.ts.*TS2322/);
  } finally {
    rmSync(directory, { recursive: true });
  }
});

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  classifyFiles,
  fileProblems,
  stageFiles,
  readTransfer,
  acknowledgeTransfer,
  clearTransfers,
  transferScope,
} from './intake';
import { readRecent, recordRecent } from './recent';
import { commandsFor, matchingCommands } from './commands';
import { parseOverview, taskCounts, todoEntries, parseRuns } from './model';
import { readWorkspaceGuards, setWorkspaceTransition } from '../../state/workspace-guard';

const scope = { userId: 'u1', tenantId: 'a', scopeKey: 'local' };
beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  clearTransfers();
  setWorkspaceTransition('idle');
});
afterEach(() => {
  clearTransfers();
  vi.restoreAllMocks();
});
it('maps document, spreadsheet and CSV without losing a mixed batch', () => {
  expect(classifyFiles([new File(['a'], 'one.docx')])).toBe('materials');
  expect(classifyFiles([new File(['a'], 'one.xlsx')])).toBe('datasets');
  expect(classifyFiles([new File(['a'], 'one.csv')])).toBe('choice');
  expect(classifyFiles([new File(['a'], 'one.csv'), new File(['b'], 'two.md')])).toBe('choice');
  expect(classifyFiles([new File(['a'], 'one.pdf'), new File(['b'], 'two.xlsx')])).toBe('mixed');
  expect(classifyFiles([new File(['a'], 'one.exe')])).toBe('unsupported');
});
it('validates whole destination batches including limits and CSV support', () => {
  expect(fileProblems([new File(['a'], 'one.csv')], 'materials')).toEqual([]);
  expect(fileProblems([new File(['a'], 'one.doc')], 'materials')).not.toEqual([]);
  expect(
    fileProblems(
      Array.from({ length: 21 }, (_, i) => new File(['a'], `${i}.csv`)),
      'datasets',
    ),
  ).not.toEqual([]);
  expect(fileProblems([new File([], 'empty.md')], 'materials')).not.toEqual([]);
});
it('keeps File objects only in memory with exact identity and scope binding', () => {
  const file = new File(['sensitive content'], 'document.md');
  const storage = vi.spyOn(Storage.prototype, 'setItem');
  const id = stageFiles(scope, 'materials', [file]);
  expect(readTransfer(id, scope, 'materials')?.files[0]).toBe(file);
  for (const wrong of [
    { ...scope, userId: 'u2' },
    { ...scope, tenantId: 'b' },
    { ...scope, scopeKey: 'group' },
  ])
    expect(readTransfer(id, wrong, 'materials')).toBeNull();
  expect(readTransfer(id, scope, 'datasets')).toBeNull();
  expect(storage).not.toHaveBeenCalled();
  expect(readWorkspaceGuards().dirtyCount).toBe(1);
});
it('supports repeated StrictMode reads and acknowledgement, and clears on scope exit', () => {
  const id = stageFiles(scope, 'materials', [new File(['a'], 'one.md')]);
  expect(readTransfer(id, scope, 'materials')).toEqual(readTransfer(id, scope, 'materials'));
  acknowledgeTransfer(id, scope);
  acknowledgeTransfer(id, scope);
  expect(readTransfer(id, scope, 'materials')).toBeNull();
  const next = stageFiles(scope, 'materials', [new File(['b'], 'two.md')]);
  window.dispatchEvent(new Event('ontology:scope-cleared'));
  expect(readTransfer(next, scope, 'materials')).toBeNull();
  expect(readWorkspaceGuards().dirtyCount).toBe(0);
});
it('refuses a new handoff during leaving and uses all identity fields', () => {
  expect(transferScope(scope)).not.toBe(transferScope({ ...scope, userId: 'other' }));
  setWorkspaceTransition('leaving');
  expect(() => stageFiles(scope, 'materials', [new File(['a'], 'x.md')])).toThrow();
});
it('recent metadata survives old cache cleanup while refusing another identity and untrusted paths', () => {
  recordRecent(scope, { href: '/materials/MAT-1?via=old&tab=source', title: 'A document' });
  sessionStorage.setItem('ontology-one-draft-x', 'old');
  for (const store of [sessionStorage, localStorage])
    for (const key of Object.keys(store))
      if (key.startsWith('ontology-one-') || key.startsWith('ontology-one.')) store.removeItem(key);
  expect(readRecent(scope)[0].href).toBe('/materials/MAT-1?tab=source');
  expect(readRecent({ ...scope, userId: 'u2' })).toEqual([]);
  expect(readRecent({ ...scope, tenantId: 'b' })).toEqual([]);
  recordRecent(scope, {
    href: '/materials/MAT-1?tab=source&q=private-query&token=private-query#source',
    title: 'A document',
  });
  expect(readRecent(scope)[0].href).toBe('/materials/MAT-1?tab=source#source');
  expect(JSON.stringify(readRecent(scope))).not.toContain('private-query');
  recordRecent(scope, { href: 'https://example.test', title: 'bad' });
  recordRecent(scope, { href: '/api/auth/session', title: 'bad' });
  expect(readRecent(scope)).toHaveLength(1);
});
it('bounds and deduplicates recent records and rejects malformed storage', () => {
  for (let i = 0; i < 20; i++) recordRecent(scope, { href: '/assets/' + i, title: 'Object ' + i });
  recordRecent(scope, { href: '/assets/19', title: 'Updated' });
  expect(readRecent(scope)).toHaveLength(8);
  expect(readRecent(scope)[0].title).toBe('Updated');
  for (const key of Object.keys(localStorage))
    localStorage.setItem(key, '{"userId":"someone else","items":[]}');
  expect(readRecent(scope)).toEqual([]);
});
it('commands only link built destinations and require edit/review/admin permission', () => {
  const viewer = commandsFor({ canEdit: false, canReview: false, manageUsers: false, federation: false });
  expect(viewer.some((item) => item.href.includes('upload=1'))).toBe(false);
  expect(viewer.some((item) => item.href === '/admin/users')).toBe(false);
  const admin = commandsFor({ canEdit: true, canReview: true, manageUsers: true, federation: true });
  expect(admin.some((item) => item.href === '/materials?upload=1')).toBe(true);
  expect(admin.some((item) => item.href === '/datasets?upload=1')).toBe(true);
  expect(matchingCommands(admin, '/').length).toBe(admin.length);
  expect(matchingCommands(admin, '/no-match')).toEqual([]);
  expect(admin.every((item) => !item.href.includes('/build/board'))).toBe(true);
});
it('preserves overview business organization counts and validates numbers', () => {
  const data = parseOverview({
    assets: 10,
    relations: 5,
    counts: { organization: 7, data: 3, rule: 7 },
    data_rows: 42,
    relation_statuses: { candidate: 2, confirmed: 3 },
  });
  expect(data.organizations).toBe(7);
  expect(data.rows).toBe(42);
  expect(() => parseOverview({ assets: '10' })).toThrow();
});
it('labels institution-wide task counts and gives each role the matching filtered destinations', () => {
  const counts = taskCounts({
    tasks: [{ status: 'active' }, { status: 'review' }, { status: 'returned' }, { status: 'completed' }],
  });
  expect(counts.active).toBe(3);
  const modeler = todoEntries({ canEdit: true, canReview: false }, counts, 8, 2, 5);
  expect(modeler.some((entry) => entry.href === '/work?status=active' && entry.count === 3)).toBe(true);
  expect(modeler.some((entry) => entry.href.includes('review_status=review'))).toBe(false);
  const reviewer = todoEntries({ canEdit: false, canReview: true }, counts, 8, 2, 5);
  expect(reviewer.some((entry) => entry.count === 8 && entry.href.includes('review_status=review'))).toBe(
    true,
  );
  expect(reviewer.some((entry) => entry.href.includes('relation_status=candidate'))).toBe(true);
});
it('deduplicates running previews and never counts completed entries as running', () => {
  const a = {
    items: [
      { id: 'r1', kind: 'application', status: 'running', title: 'Run' },
      { id: 'r2', kind: 'skill_author', status: 'completed', title: 'Done' },
    ],
  };
  expect(parseRuns([a, a])).toHaveLength(1);
});

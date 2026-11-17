import { describe, expect, it } from 'vitest';
import { activityHref, type HarnessActivityItem } from '@legacy/components/HarnessActivity';
import { terms } from '../i18n/zh-CN';

const run = 'RCP-' + 'a'.repeat(24);
const changeset = 'CS-' + 'b'.repeat(24);
const queued: HarnessActivityItem = {
  id: run,
  run_id: run,
  kind: 'recipe_run',
  title: terms.labels.r2_intake_nav,
  status: 'queued',
  href: '/build/intake?run=' + run,
};

describe('owned recipe task destinations', () => {
  it('resumes the queued recipe and opens its completed changeset', () => {
    expect(activityHref(queued)).toBe('/build/intake?run=' + run);
    expect(activityHref({ ...queued, status: 'completed', href: '/build/changesets/' + changeset })).toBe(
      '/build/changesets/' + changeset,
    );
    expect(() => expect(activityHref(queued)).toBe('')).toThrow();
  });

  it.each([
    '/build',
    '/build/intake',
    '/build/intake?run=' + run + '&redirect=/admin',
    '/build/intake?run=' + run + '#other',
    '/build/intake?run=RCP-' + 'c'.repeat(24),
    '/build/intake?run=RCP-short',
    '/build/intake/extra?run=' + run,
    '/build/changesets/' + changeset + '/edit',
    '/build/changesets/' + changeset + '?next=/admin',
    '/build/changesets/' + changeset + '\n',
    '/build/changesets/CS-short',
    '/build/changesets/%2e%2e/admin',
    '/build/changesets/' + changeset + '%2fadmin',
    '/build-lookalike/intake?run=' + run,
    'https://external.test/build/intake?run=' + run,
    '//external.test/build/intake?run=' + run,
    '/build\\intake?run=' + run,
    '/build/intake?run=' + run + '\u007f',
  ])('rejects unsupported, mismatched or unsafe destination %s', (href) => {
    expect(activityHref({ ...queued, href })).toBe('');
  });

  it('does not give other task adapters recipe destinations or alter old material links', () => {
    expect(activityHref({ ...queued, kind: 'material_extract' })).toBe('');
    expect(activityHref({ ...queued, kind: 'material_extract', href: '/materials/original' })).toBe(
      '/materials/original',
    );
    expect(activityHref({ ...queued, status: 'failed', href: '/materials/original' })).toBe(
      '/materials/original',
    );
  });
});

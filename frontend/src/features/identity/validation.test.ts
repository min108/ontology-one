import { terms } from '../../i18n/zh-CN';
import { describe, expect, it } from 'vitest';
import { validPassword, validUsername, validReason, allowedGrantAccess, excludedParents } from './validation';

describe('identity form prevalidation', () => {
  it.each(['Abc123456789', 'letters and 123'])('accepts a valid password without trimming: %s', (value) =>
    expect(validPassword(value)).toBe(true),
  );
  it.each(['short1', 'abcdefghijkl', '123456789012', 'x1'.repeat(65)])(
    'rejects invalid password: %s',
    (value) => expect(validPassword(value)).toBe(false),
  );
  it.each(['abc', ' Group.Admin ', 'a_b-c@domain.test'])('accepts normalized account names: %s', (value) =>
    expect(validUsername(value)).toBe(true),
  );
  it.each(['ab', '_abc', 'a b', terms.labels.identity_username, 'a'.repeat(81)])(
    'rejects invalid account names: %s',
    (value) => expect(validUsername(value)).toBe(false),
  );
  it('requires meaningful reasons only when required or supplied', () => {
    expect(validReason('', false)).toBe(true);
    expect(validReason('', true)).toBe(false);
    expect(validReason(' x ', false)).toBe(false);
    expect(validReason('review', true)).toBe(true);
    expect(validReason('x'.repeat(501), true)).toBe(false);
  });
  it('preserves the existing cross-institution grant restrictions', () => {
    expect(
      allowedGrantAccess({ home_tenant_id: 'hq', role: 'admin' }, 'headquarters', {
        id: 'dept',
        category: 'headquarters_department',
      }),
    ).toEqual(['read_published', 'read_private', 'write']);
    expect(
      allowedGrantAccess({ home_tenant_id: 'hq', role: 'modeler' }, 'headquarters', {
        id: 'sub',
        category: 'subsidiary',
      }),
    ).toEqual(['read_published', 'read_private']);
    expect(
      allowedGrantAccess({ home_tenant_id: 'dept', role: 'admin' }, 'headquarters_department', {
        id: 'hq',
        category: 'headquarters',
      }),
    ).toEqual(['read_published']);
    expect(
      allowedGrantAccess({ home_tenant_id: 'dept', role: 'admin' }, 'headquarters_department', {
        id: 'sub',
        category: 'subsidiary',
      }),
    ).toEqual([]);
    expect(
      allowedGrantAccess({ home_tenant_id: 'sub', role: 'admin' }, 'subsidiary', {
        id: 'hq',
        category: 'headquarters',
      }),
    ).toEqual([]);
    expect(
      allowedGrantAccess({ home_tenant_id: 'hq', role: 'admin' }, 'headquarters', {
        id: 'hq',
        category: 'headquarters',
      }),
    ).toEqual([]);
  });
  it('keeps the root option available for a new organization', () => {
    expect(excludedParents([{ id: 'hq', parent_id: null }], '')).toEqual(new Set());
  });
  it('excludes self and descendants as possible organization parents', () => {
    expect(
      excludedParents(
        [
          { id: 'a', parent_id: null },
          { id: 'b', parent_id: 'a' },
          { id: 'c', parent_id: 'b' },
          { id: 'other', parent_id: null },
        ],
        'a',
      ),
    ).toEqual(new Set(['a', 'b', 'c']));
  });
});

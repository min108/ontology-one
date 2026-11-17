import { describe, expect, it } from 'vitest';
import { changesetGate, groupItems, isHandledElsewhere } from './model';
import type { Item, Proposal } from './shared';
const item = (decision: string, extra: Partial<Item> = {}): Item => ({
  id: decision,
  tier: 'check',
  decision,
  one_liner: '',
  rationale: '',
  content_origin: 'local',
  after_data: { id: 'candidate', kind: 'rule', title: '', content: '' },
  evidence: [],
  validation: { level: 'ok', issues: [] },
  ...extra,
});
const proposal = (items: Item[], can_submit = true): Proposal => ({
  id: 'cs',
  title: '',
  summary: '',
  status: 'open',
  items,
  can_submit,
  source_text: '',
  source_material_id: '',
});
describe('R3 submit decision table: all items handled and server authorization required', () => {
  it.each([
    ['accepted', [item('accepted')]],
    ['edited', [item('edited')]],
    ['rejection alongside accepted', [item('accepted'), item('rejected')]],
    ['elsewhere does not block accepted', [item('accepted'), item('processed_elsewhere')]],
    [
      'legacy elsewhere flag does not block',
      [item('accepted'), item('pending', { processed_elsewhere: true })],
    ],
  ])('allows %s', (_name, items) =>
    expect(changesetGate(proposal(items as Item[]), true).allowed).toBe(true),
  );
  it.each([
    ['pending mixed with accepted', proposal([item('accepted'), item('pending')]), true],
    ['pending only', proposal([item('pending')]), true],
    ['server denies', proposal([item('accepted')], false), true],
    ['readonly', proposal([item('accepted')]), false],
    ['no items', proposal([]), true],
    ['only rejected', proposal([item('rejected')]), true],
    ['only elsewhere', proposal([item('processed_elsewhere')]), true],
  ])('blocks %s', (_name, value, canEdit) =>
    expect(changesetGate(value as Proposal, canEdit as boolean).allowed).toBe(false),
  );
});
it('orders checks before doubts and high confidence, preserving selection identity', () => {
  const rows = [item('accepted', { tier: 'sure' }), item('pending', { tier: 'doubt' }), item('edited')];
  expect(groupItems(rows).map((group) => group.tier)).toEqual(['check', 'doubt', 'sure']);
  expect(groupItems(rows).flatMap((group) => group.items)).toEqual([rows[2], rows[1], rows[0]]);
  expect(isHandledElsewhere(item('pending', { processed_elsewhere: true }))).toBe(true);
});

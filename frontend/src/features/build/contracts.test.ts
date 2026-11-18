import { describe, expect, it } from 'vitest';
import { changesetGate, groupItems, isHandledElsewhere, readyForBulkAccept } from './model';
import { gates } from './gateFixtures';
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
  document_facts: { valid_from: { value: '', origin: 'source' }, scope: { value: '', origin: 'source' } },
});
describe('R4-A.1 D13: eligible accepted items may submit independently with server authorization', () => {
  it.each([
    ['accepted', [item('accepted')]],
    ['pending mixed with accepted', [item('accepted'), item('pending')]],
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
  expect(groupItems(rows).map((group) => group.tier)).toEqual(['incomplete', 'check', 'doubt', 'sure']);
  expect(groupItems(rows).flatMap((group) => group.items)).toEqual([rows[2], rows[1], rows[0]]);
  expect(isHandledElsewhere(item('pending', { processed_elsewhere: true }))).toBe(true);
});

it('never labels an incomplete or explicitly ineligible item as bulk ready', () => {
  const complete = item('pending', { tier: 'sure', gates: gates(), ready_for_bulk_accept: true });
  const incomplete = { ...complete, gates: gates(['QG2']), ready_for_bulk_accept: false };
  expect(readyForBulkAccept(complete)).toBe(true);
  for (const negative of [
    incomplete,
    { ...complete, ready_for_bulk_accept: false },
    { ...complete, validation: { level: 'error', issues: [] } },
    { ...complete, decision: 'accepted' },
  ])
    expect(readyForBulkAccept(negative)).toBe(false);
  expect(groupItems([incomplete]).find((group) => group.tier === 'sure')?.items).toEqual([]);
  expect(groupItems([incomplete])[0].items).toEqual([incomplete]);
});
it('counts partial submission separately from already submitted and blocked drafts', () => {
  const ready = item('accepted', {
    id: 'ready',
    can_submit: true,
    applied_entity_id: 'R-1',
    applied_status: 'draft',
  });
  const blocked = item('accepted', {
    id: 'blocked',
    can_submit: false,
    applied_entity_id: 'R-2',
    applied_status: 'draft',
    gates: gates(['QG2']),
  });
  const submitted = item('accepted', {
    id: 'submitted',
    can_submit: true,
    applied_entity_id: 'R-3',
    applied_status: 'review',
  });
  expect(changesetGate(proposal([ready, blocked, submitted, item('pending')]), true)).toMatchObject({
    allowed: true,
    ready: 1,
    remaining: [blocked],
    submitted: 1,
    pending: 1,
  });
  expect(changesetGate(proposal([blocked, submitted]), true).allowed).toBe(false);
  expect(changesetGate(proposal([ready], false), true).allowed).toBe(false);
});

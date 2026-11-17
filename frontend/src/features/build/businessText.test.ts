import sourceLocations from '../../../test-fixtures/source-locations.json';
import { expect, it } from 'vitest';
import { sourceDescription, sourceLocator } from './businessText';

it.each([
  [sourceLocations.characterRange, ''],
  [sourceLocations.rangeWithClause, sourceLocations.clause],
  [sourceLocations.clause, sourceLocations.clause],
  ['Amount column', 'Amount column'],
  [null, ''],
])('separates technical offsets from business source labels: %j', (value, expected) => {
  expect(sourceLocator(value)).toBe(expected);
});

it.each([
  ['Historical source', 'Historical source'],
  [{ excerpt: 'Historical source' }, 'Historical source'],
  [null, ''],
  [
    { title: 'Payment policy', locator: sourceLocations.characterRange, quote: 'Review before payment.' },
    'Payment policy · Review before payment.',
  ],
  [
    { title: 'Payment policy', locator: sourceLocations.clause, excerpt: 'Check the evidence.' },
    `Payment policy · ${sourceLocations.clause} · Check the evidence.`,
  ],
])('reads historical source shapes without exposing internal locations: %j', (value, expected) => {
  expect(sourceDescription(value)).toBe(expected);
});

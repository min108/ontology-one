import { expect, it } from 'vitest';
import { selectableEntities } from './selectableEntities';

const material = { id: 'material-A', kind: 'material', title: 'Original regulation', payload: {} };
const chunk = (id: string) => ({ ...material, id, payload: { chunk_id: id, material_id: material.id } });
it('preserves first-hit order and returns one selectable original across chunks and entity hits', () => {
  const dataset = { id: 'D', kind: 'dataset', title: 'Register', payload: {} };
  expect(selectableEntities([chunk('c-1'), dataset, material, chunk('c-2')]).map((hit) => hit.id)).toEqual([
    material.id,
    dataset.id,
  ]);
  expect(selectableEntities([dataset, material, chunk('c-1')])).toEqual([dataset, material]);
});
it('drops an unresolvable chunk without rewriting similarly named real entities or unrelated kinds', () => {
  const bad = { ...material, id: 'c', payload: { chunk_id: 'c' } };
  const real = { ...material, id: 'chunk:original', payload: { chunk_id: 'other', material_id: 'M' } };
  const data = { ...chunk('dataset-id'), kind: 'dataset' };
  expect(selectableEntities([bad, real, data])).toEqual([real, data]);
});

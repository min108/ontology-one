import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { invokeCapability } from '../../api/client';
import type { ElementDetail } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { ElementAliases } from './ElementAliases';

vi.mock('../../api/client', () => ({ invokeCapability: vi.fn() }));
function detail(): ElementDetail {
  return {
    can_edit: true,
    versions: [],
    card: { one_liner: '', points: [], relations: [], usage: '' },
    entity: {
      id: 'R1',
      kind: 'rule',
      title: '政策名称',
      status: 'published',
      version: 7,
      domain: '',
      activities: [],
      _meta: { pillar: 'asset' },
      payload: { aliases: '原有称呼', source_refs: [{ anchor_id: 'original' }], conditions: ['原有条件'] },
    },
  };
}
it('requires an authorized ordinary asset and a writable lifecycle state', () => {
  for (const mutate of [
    (d: ElementDetail) => {
      d.can_edit = false;
    },
    (d: ElementDetail) => {
      d.entity.status = 'review';
    },
    (d: ElementDetail) => {
      d.entity._meta = { pillar: 'source' };
    },
    (d: ElementDetail) => {
      d.entity.payload.managed_skill = true;
    },
    (d: ElementDetail) => {
      d.entity.payload.authority_spec = {};
    },
    (d: ElementDetail) => {
      d.entity.payload.active_asset = false;
    },
  ]) {
    const d = detail();
    mutate(d);
    const { unmount } = render(<ElementAliases detail={d} disabled={false} onSaved={vi.fn()} />);
    expect(screen.queryByRole('button', { name: terms.labels.r4b_alias_register })).toBeNull();
    unmount();
  }
});
it('keeps original scalar aliases and all other payload fields, prevents duplicate or empty saves, and uses the latest revision', async () => {
  vi.mocked(invokeCapability).mockResolvedValue({});
  const current = detail();
  const onSaved = vi.fn();
  const view = render(<ElementAliases detail={current} disabled={false} onSaved={onSaved} />);
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r4b_alias_register }));
  const input = screen.getByRole('textbox', { name: terms.labels.r4b_alias_new });
  const revise = screen.getByRole('button', { name: terms.actions.catalog_2_8 });
  expect(revise).toBeDisabled();
  fireEvent.change(input, { target: { value: ' 原有称呼 ' } });
  expect(revise).toBeDisabled();
  fireEvent.change(input, { target: { value: ' 另一个名称 ' } });
  fireEvent.click(revise);
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(vi.mocked(invokeCapability).mock.calls[0]).toEqual([
    'element.start_revision',
    { entity_id: 'R1', version: 7, reason: terms.labels.r4b_alias_register },
    { signal: expect.any(AbortSignal) },
  ]);
  const draft = { ...current, entity: { ...current.entity, status: 'draft', version: 8 } };
  view.rerender(<ElementAliases detail={draft} disabled={false} onSaved={onSaved} />);
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_2_0 }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(terms.labels.r4b_alias_saved));
  expect(vi.mocked(invokeCapability).mock.calls[1]).toEqual([
    'element.update_draft',
    {
      entity_id: 'R1',
      version: 8,
      reason: terms.labels.r4b_alias_register,
      payload: { ...current.entity.payload, aliases: ['原有称呼', '另一个名称'] },
    },
    { signal: expect.any(AbortSignal) },
  ]);
});

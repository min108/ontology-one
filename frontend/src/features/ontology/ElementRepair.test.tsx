import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { invokeCapability } from '../../api/client';
import type { ElementDetail } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { gates } from '../build/gateFixtures';
import { ElementRepair } from './ElementRepair';

vi.mock('../../api/client', () => ({ invokeCapability: vi.fn() }));
const copy = terms.labels;
function detail(): ElementDetail {
  const checks = gates(['QG1', 'QG2']);
  checks.ready_to_submit = false;
  checks.fields = [
    {
      key: 'definition',
      label: '定义',
      value: '',
      source: 'missing',
      source_quote: '',
      confirmed_by: '',
      href: '',
    },
  ];
  return {
    entity: {
      id: 'data-1',
      kind: 'data',
      title: '实际付款',
      summary: '',
      domain: '',
      status: 'draft',
      version: 2,
      activities: [],
      payload: { unit: '元', grain: '每笔付款', historical_note: '保留内容' },
    },
    card: { one_liner: '', points: [], relations: [], usage: '' },
    versions: [],
    can_edit: true,
    gates: checks,
  };
}
beforeEach(() => vi.clearAllMocks());
it('keeps a missing definition non-waivable and requires saving the exact application before submit', async () => {
  const current = detail(),
    onSaved = vi.fn();
  const { container } = render(
    <MemoryRouter>
      <ElementRepair detail={current} disabled={false} onSaved={onSaved} />
    </MemoryRouter>,
  );
  expect(screen.getByRole('button', { name: copy.r4a1_legacy_submit })).toBeDisabled();
  expect(container.querySelector('[data-waiver-application-code="QG2"]')).toBeNull();
  fireEvent.change(screen.getByLabelText('定义'), { target: { value: '每笔实际支付的人民币金额' } });
  fireEvent.change(
    screen.getByLabelText(current.gates!.checks[0].label + ' · ' + copy.r4a1_legacy_application),
    { target: { value: '手工登记，暂不能定位原始凭证；请核对银行回单。' } },
  );
  fireEvent.click(screen.getByRole('button', { name: copy.r4a1_legacy_save }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(copy.r4a1_legacy_saved));
  expect(invokeCapability).toHaveBeenCalledWith(
    'element.update_draft',
    expect.objectContaining({
      version: 2,
      payload: { ...current.entity.payload, definition: '每笔实际支付的人民币金额' },
      waiver_applications: { QG1: '手工登记，暂不能定位原始凭证；请核对银行回单。' },
    }),
    expect.anything(),
  );
  expect(vi.mocked(invokeCapability).mock.calls.some(([name]) => name === 'element.submit_draft')).toBe(
    false,
  );
});
it('uses current server submission permission, and unsaved changes cannot submit', async () => {
  const current = detail();
  current.gates!.ready_to_submit = true;
  const { rerender } = render(
    <MemoryRouter>
      <ElementRepair detail={current} disabled={false} onSaved={() => undefined} />
    </MemoryRouter>,
  );
  expect(screen.getByRole('button', { name: copy.r4a1_legacy_submit })).toBeEnabled();
  fireEvent.change(screen.getByLabelText('定义'), { target: { value: '尚未保存的定义' } });
  expect(screen.getByRole('button', { name: copy.r4a1_legacy_submit })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: copy.r4a1_legacy_submit }));
  expect(invokeCapability).not.toHaveBeenCalled();
  rerender(
    <MemoryRouter>
      <ElementRepair key="fresh" detail={current} disabled onSaved={() => undefined} />
    </MemoryRouter>,
  );
  expect(screen.getByRole('button', { name: copy.r4a1_legacy_submit })).toBeDisabled();
});

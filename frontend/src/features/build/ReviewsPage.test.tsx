import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import type { Review } from '../../api/generated/types';
import { testQuery, testSession } from './testQuery';
import ReviewsPage from './ReviewsPage';
vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: vi.fn(),
}));
const row = (): Review => ({
  entity: {
    id: 'rule-1',
    title: '付款核对规则',
    summary: '支付前核对合同',
    status: 'review',
    kind: 'rule',
    version: 3,
    payload: { content: '支付前核对合同与验收记录', conditions: ['付款申请前'] },
  },
  submitter: '建设人员乙',
  submitted_at: '2026-11-17T01:00:00Z',
  can_decide: true,
  blocked_reason: '',
  source_refs: [
    { material_id: 'm', anchor_id: 'a', source_version: 'v', quote: '支付前核对合同', start: 0, end: 7 },
  ],
});
let current: Review;
let available: boolean;
beforeEach(() => {
  testSession();
  vi.clearAllMocks();
  current = row();
  available = true;
  vi.mocked(api).mockImplementation(async (path, init) => {
    if (init?.method === 'POST') {
      available = false;
      current.entity.status = JSON.parse(String(init.body)).decision === 'return' ? 'returned' : 'published';
      return { data: current } as never;
    }
    if (path.includes('/source?')) return { text: '支付前核对合同', focus: { start: 0, end: 7 } } as never;
    return { data: { items: available ? [current] : [], total: available ? 1 : 0 } } as never;
  });
});
function Location() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}
function open() {
  return render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <MemoryRouter initialEntries={['/govern/reviews']}>
        <ReviewsPage />
        <Location />
      </MemoryRouter>
    </ConfigProvider>,
    { wrapper: testQuery() },
  );
}
it('shows a clear self-review reason and blocks both decision paths', async () => {
  current.can_decide = false;
  current.blocked_reason = '提交人与最后编辑人需要交由其他审核人员审核。';
  open();
  expect(await screen.findByText(terms.labels.r3_main_review_self)).toBeVisible();
  expect(screen.getByRole('button', { name: terms.actions.catalog_2_5 })).toBeDisabled();
  expect(screen.getByRole('button', { name: terms.actions.catalog_2_6 })).toBeDisabled();
  expect(vi.mocked(api).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
});
it('requires a reason inside one return confirmation and preserves the returned reason in the result', async () => {
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_2_6 }));
  const dialog = screen.getByRole('dialog');
  const confirm = within(dialog).getByRole('button', { name: terms.actions.catalog_2_6 });
  expect(confirm).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText(terms.labels.r3_main_return_reason), {
    target: { value: '补充验收资料的适用范围' },
  });
  fireEvent.click(confirm);
  await waitFor(() => expect(current.entity.status).toBe('returned'));
  expect(api).toHaveBeenCalledWith(
    '/v2/reviews/rule-1:decide',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ decision: 'return', version: 3, reason: '补充验收资料的适用范围' }),
    }),
  );
  expect(await screen.findByText(/退回理由：补充验收资料的适用范围/)).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('publishes only after L2 confirmation and asks with the published reference scope', async () => {
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_2_5 }));
  expect(current.entity.status).toBe('review');
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', { name: terms.actions.catalog_2_5 }),
  );
  await waitFor(() => expect(current.entity.status).toBe('published'));
  fireEvent.click((await screen.findAllByRole('button', { name: terms.labels.r3_main_ask }))[0]);
  expect(screen.getByTestId('location')).toHaveTextContent('/applications?element=rule-1');
});
it('keeps original evidence in the current review drawer', async () => {
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.labels.r3_main_view_original }));
  const drawer = screen.getByRole('dialog');
  await waitFor(() => expect(drawer.querySelector('mark')).toHaveTextContent('支付前核对合同'));
  expect(screen.getByTestId('location')).toHaveTextContent('/govern/reviews');
  expect(within(drawer).getByRole('link', { name: terms.labels.r3_main_source_page })).toHaveAttribute(
    'href',
    '/materials/m?anchor=a&source_version=v',
  );
});

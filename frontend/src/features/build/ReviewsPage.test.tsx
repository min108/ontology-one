import legacyFixture from '../../../test-fixtures/r4a1-legacy-content.json';
import fixture from '../../../test-fixtures/r4-semantic-content.json';
import sourceLocations from '../../../test-fixtures/source-locations.json';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { focusManager } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
import type { Review } from '../../api/generated/types';
import { testQuery, testSession } from './testQuery';
import ReviewsPage from './ReviewsPage';
import { gates } from './gateFixtures';
vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: vi.fn(),
}));
const row = (): Review => ({
  entity: {
    id: 'rule-1',
    title: 'Payment control',
    summary: 'Review before payment',
    status: 'review',
    kind: 'rule',
    version: 3,
    payload: {
      content: 'Review the contract and acceptance records',
      conditions: ['Before requesting payment'],
    },
  },
  submitter: 'Builder B',
  submitted_at: '2026-11-17T01:00:00Z',
  can_decide: true,
  blocked_reason: '',
  source_refs: [
    {
      material_id: 'm',
      anchor_id: 'a',
      source_version: 'v',
      quote: 'Review before payment',
      start: 0,
      end: 'Review before payment'.length,
    },
  ],
});
let current: Review;
let available: boolean;
beforeEach(() => {
  testSession();
  useUiStore.setState({ viewMode: 'simple' });
  focusManager.setFocused(undefined);
  vi.clearAllMocks();
  current = row();
  available = true;
  vi.mocked(api).mockImplementation(async (path, init) => {
    if (init?.method === 'POST') {
      available = false;
      current.entity.status = JSON.parse(String(init.body)).decision === 'return' ? 'returned' : 'published';
      return { data: current } as never;
    }
    if (path.includes('/source?'))
      return {
        text: 'Review before payment',
        focus: { start: 0, end: 'Review before payment'.length },
      } as never;
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
  current.blocked_reason = terms.labels.r3_main_review_self_detail;
  open();
  expect((await screen.findAllByText(terms.labels.r3_main_review_self_detail))[0]).toBeVisible();
  expect(screen.getByRole('button', { name: terms.actions.catalog_2_5 })).toBeDisabled();
  expect(screen.getByRole('button', { name: terms.actions.catalog_2_6 })).toBeDisabled();
  expect(vi.mocked(api).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
});

it('shows the actual server denial without mislabelling it as self-review', async () => {
  current.can_decide = false;
  current.blocked_reason = 'This institution is read-only; review in the owning institution.';
  open();
  expect((await screen.findAllByText(current.blocked_reason))[0]).toBeVisible();
  expect(screen.queryByText(terms.labels.r3_main_review_self)).not.toBeInTheDocument();
  expect(screen.queryByText(terms.labels.r3_main_review_other)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: terms.actions.catalog_2_5 })).toBeDisabled();
  expect(screen.getByRole('button', { name: terms.actions.catalog_2_6 })).toBeDisabled();
});

it('keeps readable policy sources and hides character positions until professional technical details', async () => {
  current.source_refs = [
    {
      ...current.source_refs![0],
      title: 'Payment policy',
      locator: sourceLocations.characterRange,
    },
    { excerpt: 'Historical policy source' },
  ];
  const { container } = open();
  expect(await screen.findByText('Payment policy · Review before payment')).toBeVisible();
  expect(screen.getByText('Historical policy source')).toBeVisible();
  expect(container).not.toHaveTextContent(/原文第|阅读版/);
  act(() => useUiStore.setState({ viewMode: 'professional' }));
  const technical = container.querySelector('[data-technical-details]')!;
  expect(technical).toHaveTextContent(sourceLocations.characterRange);
  expect(within(technical as HTMLElement).getByText(/原文第/)).not.toBeVisible();
});

it('rechecks review permission on refocus without changing the locked target', async () => {
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_2_5 }));
  const dialog = screen.getByRole('dialog');
  current = { ...current, can_decide: false, blocked_reason: 'Review permission changed' };
  await act(async () => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
  });
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  expect(within(dialog).getByRole('button', { name: terms.actions.catalog_2_5 })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole('button', { name: terms.actions.catalog_2_5 }));
  expect(vi.mocked(api).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
});
it('requires a reason inside one return confirmation and preserves the returned reason in the result', async () => {
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_2_6 }));
  const dialog = screen.getByRole('dialog');
  const confirm = within(dialog).getByRole('button', { name: terms.actions.catalog_2_6 });
  expect(confirm).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText(terms.labels.r3_main_return_reason), {
    target: { value: 'Complete the scope of acceptance records' },
  });
  fireEvent.click(confirm);
  await waitFor(() => expect(current.entity.status).toBe('returned'));
  expect(api).toHaveBeenCalledWith(
    '/v2/reviews/rule-1:decide',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        decision: 'return',
        version: 3,
        reason: 'Complete the scope of acceptance records',
      }),
    }),
  );
  expect(
    await screen.findByText(
      `${terms.labels.r3_main_return_reason}：Complete the scope of acceptance records`,
    ),
  ).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('publishes only after L2 confirmation and asks with the published reference scope', async () => {
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_2_5 }));
  expect(current.entity.status).toBe('review');
  expect(screen.getByRole('dialog')).toHaveStyle({
    width: getComputedStyle(document.documentElement).getPropertyValue('--zy-dialog-width'),
  });
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
  await waitFor(() => expect(drawer.querySelector('mark')).toHaveTextContent('Review before payment'));
  expect(screen.getByTestId('location')).toHaveTextContent('/govern/reviews');
  expect(within(drawer).getByRole('link', { name: terms.labels.r3_main_source_page })).toHaveAttribute(
    'href',
    '/materials/m?anchor=a&source_version=v',
  );
});

it.each([
  'Applies to payment requests only',
  ['Applies to payment requests only'],
  { content: 'Applies to payment requests only', candidate_id: 'secret-condition' },
])('preserves readable legacy conditions without leaking internal fields: %j', async (conditions) => {
  Object.assign(current.entity.payload, {
    conditions,
    content: { content: [{ text: 'Review the acceptance records' }], internal_key: 'secret-content' },
  });
  open();
  expect(await screen.findByText(/Applies to payment requests only/)).toBeVisible();
  expect(screen.getByText(/Review the acceptance records/)).toBeVisible();
  expect(screen.queryByText(/secret-condition|secret-content|internal_key/)).not.toBeInTheDocument();
});
it('preserves unanchored legacy source descriptions without inventing a material link', async () => {
  Object.assign(current, {
    source_refs: [{ id: 'dataset-1', kind: 'dataset', title: 'Historical ledger', locator: 'Amount column' }],
  });
  Object.assign(current.entity.payload, { conditions: null });
  open();
  expect(await screen.findByText('Historical ledger · Amount column')).toBeVisible();
  expect(screen.queryByRole('button', { name: terms.labels.r3_main_view_original })).not.toBeInTheDocument();
  expect(screen.queryByText(terms.labels.r3_main_scope)).not.toBeInTheDocument();
});
it('accepts null legacy sources while keeping the review decision boundary', async () => {
  Object.assign(current, { source_refs: null });
  Object.assign(current.entity.payload, { content: null, definition: ['Historical business definition'] });
  open();
  expect(await screen.findByText('Historical business definition')).toBeVisible();
  expect(screen.getByText(terms.labels.r3_main_no_source)).toBeVisible();
});

it('keeps local-rule origin visible while the business content awaits review', async () => {
  current.entity.payload.extraction_mode = 'local';
  open();
  expect(await screen.findByText(terms.labels.r3_ui_local)).toBeVisible();
  expect(screen.queryByText('local')).not.toBeInTheDocument();
});

it.each(['approve_publish', 'return'] as const)(
  'locks the default review identity, version, title and %s decision across a refocus reorder',
  async (decision) => {
    const first = row();
    const second = row();
    second.entity = { ...second.entity, id: 'rule-2', title: 'Other policy', version: 8 };
    let rows = [first, second];
    vi.mocked(api).mockImplementation(async (_path, init) => {
      if (init?.method === 'POST') return { data: first } as never;
      return { data: { items: structuredClone(rows), total: rows.length } } as never;
    });
    open();
    const action = decision === 'return' ? terms.actions.catalog_2_6 : terms.actions.catalog_2_5;
    fireEvent.click(await screen.findByRole('button', { name: action }));
    const dialog = screen.getByRole('dialog');
    await waitFor(() => expect(within(dialog).getByText(first.entity.title)).toBeVisible());
    if (decision === 'return') {
      fireEvent.change(within(dialog).getByLabelText(terms.labels.r3_main_return_reason), {
        target: { value: 'Clarify the policy scope' },
      });
    }
    rows = [second, { ...first, entity: { ...first.entity, title: 'Changed title', version: 4 } }];
    await act(async () => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(within(dialog).getByRole('button', { name: action })).toBeEnabled());
    expect(within(dialog).getByText('Payment control')).toBeVisible();
    expect(within(dialog).queryByText('Other policy')).not.toBeInTheDocument();
    expect(document.querySelector('[data-review-item-id="rule-1"]')).toHaveAttribute('aria-current', 'true');
    expect(document.querySelector('[data-review-item-id="rule-2"]')).not.toHaveAttribute('aria-current');
    fireEvent.click(within(dialog).getByRole('button', { name: action }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        '/v2/reviews/rule-1:decide',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            decision,
            version: 3,
            reason: decision === 'return' ? 'Clarify the policy scope' : '',
          }),
        }),
      ),
    );
    expect(vi.mocked(api).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    focusManager.setFocused(undefined);
  },
);

it('requires real waiver reasons before approving failed source and condition checks', async () => {
  current.gates = gates(['QG1', 'QG3']);
  open();
  const approve = await screen.findByRole('button', { name: terms.actions.catalog_2_5 });
  expect(approve).toBeDisabled();
  expect(screen.getByRole('button', { name: terms.actions.catalog_2_6 })).toBeEnabled();
  fireEvent.change(screen.getByLabelText(fixture.sourceLabel + ' · ' + terms.labels.r4_gate_waiver), {
    target: { value: fixture.paperVerified },
  });
  expect(approve).toBeDisabled();
  fireEvent.change(screen.getByLabelText(fixture.conditionsLabel + ' · ' + terms.labels.r4_gate_waiver), {
    target: { value: '   ' },
  });
  expect(approve).toBeDisabled();
  fireEvent.change(screen.getByLabelText(fixture.conditionsLabel + ' · ' + terms.labels.r4_gate_waiver), {
    target: { value: fixture.scenarioAccepted },
  });
  expect(approve).toBeEnabled();
  fireEvent.click(approve);
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', { name: terms.actions.catalog_2_5 }),
  );
  await waitFor(() => expect(current.entity.status).toBe('published'));
  const post = vi.mocked(api).mock.calls.find(([, init]) => init?.method === 'POST');
  expect(JSON.parse(String(post?.[1]?.body)).waivers).toEqual({
    QG1: fixture.paperVerified,
    QG3: fixture.scenarioAccepted,
  });
});
it('cannot approve or waive a missing required relation but can return it', async () => {
  current.gates = gates(['QG4']);
  open();
  expect(await screen.findByRole('button', { name: terms.actions.catalog_2_5 })).toBeDisabled();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.getByText(terms.labels.r4_gate_cannot_waive)).toBeVisible();
  expect(screen.getByRole('button', { name: terms.actions.catalog_2_6 })).toBeEnabled();
});

it('shows source-tagged facts and application reasons again in publication confirmation', async () => {
  current.gates = gates(['QG1']);
  current.gates.checks[0].application_reason = legacyFixture.reviewApplication;
  current.gates.fields = [
    {
      key: 'valid_from',
      label: legacyFixture.dateLabel,
      value: '2026-01-01',
      source: 'source',
      source_quote: legacyFixture.dateQuote,
      confirmed_by: '',
      href: '',
    },
    {
      key: 'exceptions',
      label: legacyFixture.exceptionLabel,
      value: legacyFixture.exceptionValue,
      source: 'batch_confirmation',
      source_quote: '',
      confirmed_by: legacyFixture.reviewer,
      href: '',
    },
  ];
  open();
  expect(await screen.findByText('2026-01-01')).not.toBeVisible();
  fireEvent.click(screen.getByText(terms.labels.r4a1_legacy_facts));
  expect(screen.getByText('2026-01-01')).toBeVisible();
  expect(screen.getByText(/原件尚在归档，请先核对经办记录/)).toBeVisible();
  const approve = screen.getByRole('button', { name: terms.actions.catalog_2_5 });
  // A builder application is not a reviewer waiver.
  expect(approve).toBeDisabled();
  fireEvent.change(screen.getByLabelText(fixture.sourceLabel + ' · ' + terms.labels.r4_gate_waiver), {
    target: { value: legacyFixture.reviewWaiver },
  });
  fireEvent.click(approve);
  const dialog = screen.getByRole('dialog');
  await waitFor(() => expect(within(dialog).getByText('2026-01-01')).toBeVisible());
  expect(within(dialog).getByText(legacyFixture.dateQuote)).toBeVisible();
  expect(within(dialog).getByText(/原件尚在归档，请先核对经办记录/)).toBeVisible();
  expect(within(dialog).getByText(/已核对经办记录，批准本次出处豁免/)).toBeVisible();
});

it('keeps per-check waiver and refusal decisions disjoint when returning an application', async () => {
  current.gates = gates(['QG1', 'QG3']);
  current.gates.checks[0].application_reason = legacyFixture.reviewApplication;
  current.gates.checks[2].application_reason = legacyFixture.manualApplication;
  open();
  await screen.findByRole('button', { name: terms.actions.catalog_2_6 });
  fireEvent.change(screen.getByLabelText(fixture.sourceLabel + ' · ' + terms.labels.r4_gate_waiver), {
    target: { value: legacyFixture.reviewWaiver },
  });
  fireEvent.click(screen.getByRole('button', { name: terms.actions.catalog_2_6 }));
  const dialog = screen.getByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText(terms.labels.r3_main_return_reason), {
    target: { value: 'Complete the applicability conditions' },
  });
  fireEvent.change(
    within(dialog).getByLabelText(fixture.sourceLabel + ' · ' + terms.labels.r4a1_legacy_denial),
    { target: { value: 'Conflicting decision' } },
  );
  const confirm = within(dialog).getByRole('button', { name: terms.actions.catalog_2_6 });
  expect(confirm).toBeDisabled();
  fireEvent.click(confirm);
  expect(vi.mocked(api).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  fireEvent.change(
    within(dialog).getByLabelText(fixture.sourceLabel + ' · ' + terms.labels.r4a1_legacy_denial),
    { target: { value: '' } },
  );
  fireEvent.change(
    within(dialog).getByLabelText(fixture.conditionsLabel + ' · ' + terms.labels.r4a1_legacy_denial),
    { target: { value: 'Missing applicability boundary' } },
  );
  fireEvent.click(confirm);
  await waitFor(() => expect(current.entity.status).toBe('returned'));
  const post = vi.mocked(api).mock.calls.find(([, init]) => init?.method === 'POST');
  expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
    waivers: { QG1: legacyFixture.reviewWaiver },
    waiver_denials: { QG3: 'Missing applicability boundary' },
  });
});

import sourceLocations from '../../../test-fixtures/source-locations.json';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { focusManager } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError, invokeCapability } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
import { sentence } from './model';
import { testQuery, testSession } from './testQuery';
import ChangesetPage from './ChangesetPage';
import { gates } from './gateFixtures';

vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: vi.fn(),
  invokeCapability: vi.fn(),
}));
vi.mock('./shared', async (original) => ({
  ...(await original<typeof import('./shared')>()),
  useBuildIdentity: () => ({ key: 'test-owner', canEdit: true, canRead: true }),
}));
const seed = () => ({
  id: 'CS-1',
  origin_ref: 'run-1',
  title: 'Policy',
  summary: 'Summary',
  status: 'open',
  source_text: 'Original text',
  source_material_id: 'M-1',
  can_submit: false,
  submit_blockers: [] as string[],
  items: [
    {
      id: 'item-1',
      tier: 'sure',
      decision: 'pending',
      one_liner: 'Original conclusion',
      content_origin: 'model',
      validation: { level: 'ok', issues: [] },
      evidence: [],
      after_data: {
        id: 'candidate-1',
        title: 'Original title',
        content: 'Original content',
        kind: 'rule',
        summary: 'Internal candidate summary',
        source_quotes: [],
      },
      applied_entity_id: '',
      gates: gates(),
    },
  ],
});
let proposal = seed();
beforeEach(() => {
  testSession();
  useUiStore.setState({ viewMode: 'simple' });
  vi.clearAllMocks();
  proposal = seed();
  vi.mocked(api).mockImplementation(async () => ({ data: structuredClone(proposal) }) as never);
});
function open() {
  return render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <MemoryRouter initialEntries={['/build/changesets/CS-1']}>
        <Routes>
          <Route path="/build/changesets/:id" element={<ChangesetPage />} />
        </Routes>
      </MemoryRouter>
    </ConfigProvider>,
    { wrapper: testQuery() },
  );
}
it('sends only editable fields, shows failure inside the dialog, refreshes and retains the page', async () => {
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_1_1 }));
  const heading = screen.getByRole('heading', { name: terms.labels.r2_changeset_title });
  const modal = screen.getByRole('dialog');
  fireEvent.change(within(modal).getByLabelText(terms.labels.r2_title), {
    target: { value: 'Revised title' },
  });
  fireEvent.change(within(modal).getByLabelText(terms.labels.r2_content), {
    target: { value: 'Revised content' },
  });
  vi.mocked(invokeCapability).mockRejectedValueOnce(new ApiError('Edit failed', 422));
  const save = within(modal).getByRole('button', { name: terms.actions.r2_save });
  fireEvent.click(save);
  expect(await within(modal).findByRole('alert')).toHaveTextContent('Edit failed');
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  expect(screen.getByRole('heading', { name: terms.labels.r2_changeset_title })).toBe(heading);
  expect(invokeCapability).toHaveBeenCalledWith(
    'changeset.decide_items',
    {
      changeset_id: 'CS-1',
      item_ids: ['item-1'],
      decision: 'edited',
      edit: { title: 'Revised title', content: 'Revised content', kind: 'rule' },
    },
    expect.anything(),
  );
  vi.mocked(invokeCapability).mockImplementation(async () => {
    proposal.items[0].after_data.title = 'Revised title';
    proposal.items[0].after_data.content = 'Revised content';
    proposal.items[0].applied_entity_id = 'rule-1';
    proposal.can_submit = true;
    return {} as never;
  });
  fireEvent.click(save);
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(await screen.findByRole('heading', { name: 'Revised title' })).toBeVisible();
  expect(screen.getByText('Original conclusion')).toBeVisible();
});
it('disables submission when the server reports no remaining draft to submit', async () => {
  proposal.items[0].decision = 'accepted';
  proposal.items[0].applied_entity_id = 'rule-1';
  open();
  expect(await screen.findByRole('button', { name: terms.labels.r3_main_submitted })).toBeDisabled();
  expect(screen.getByText(sentence(terms.labels.r31_submitted_count, { count: 1 }))).toBeVisible();
  expect(
    screen.queryByText(sentence(terms.labels.r3_main_submit_count, { count: 1 })),
  ).not.toBeInTheDocument();
});

it('expires the undo notice together with its action', async () => {
  const timers: (() => void)[] = [];
  const original = globalThis.setTimeout;
  const timeout = vi.spyOn(globalThis, 'setTimeout').mockImplementation((callback, delay, ...args) => {
    if (delay === 8000 && typeof callback === 'function') timers.push(callback as () => void);
    return original(callback, delay, ...args);
  });
  try {
    vi.mocked(invokeCapability).mockImplementation(async () => {
      proposal.items[0].decision = 'rejected';
      return {} as never;
    });
    open();
    fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_1_2 }));
    expect(await screen.findByText(terms.labels.r3_main_undo_saved)).toBeVisible();
    expect(screen.getByRole('button', { name: terms.labels.r3_main_undo })).toBeVisible();
    expect(timers).toHaveLength(1);
    act(() => timers[0]());
    expect(screen.queryByText(terms.labels.r3_main_undo_saved)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: terms.labels.r3_main_undo })).not.toBeInTheDocument();
  } finally {
    timeout.mockRestore();
  }
});

it('keeps source positions out of simple view and inside the professional technical fold', async () => {
  proposal.items[0].evidence = [
    {
      material_id: 'M-1',
      anchor_id: 'A-1',
      source_version: 'V-1',
      quote: 'Original',
      start: 0,
      end: 8,
      locator: sourceLocations.characterRange,
    },
  ] as never;
  const { container } = open();
  expect((await screen.findAllByText('Policy'))[0]).toBeVisible();
  expect(container).not.toHaveTextContent(/原文第|阅读版/);
  act(() => useUiStore.setState({ viewMode: 'professional' }));
  const technical = container.querySelector('[data-technical-details]')!;
  expect(technical).toHaveTextContent(sourceLocations.characterRange);
  expect(within(technical as HTMLElement).getByText(sourceLocations.characterRange)).not.toBeVisible();
});

it('requires all items handled before one L2 submit confirmation and then submits once', async () => {
  proposal.can_submit = true;
  open();
  const submit = await screen.findByRole('button', { name: terms.actions.catalog_1_6 });
  expect(submit).toBeDisabled();
  expect(screen.getByText(sentence(terms.labels.r3_main_pending, { count: 1 }))).toBeVisible();
  vi.mocked(invokeCapability).mockImplementation(async (operation) => {
    if (operation === 'changeset.decide_items') {
      proposal.items[0].decision = 'accepted';
      proposal.items[0].applied_entity_id = 'rule-1';
    }
    if (operation === 'changeset.apply_and_submit') proposal.can_submit = false;
    return {} as never;
  });
  fireEvent.click(screen.getByRole('button', { name: terms.actions.catalog_1_0 }));
  await waitFor(() => expect(submit).toBeEnabled());
  expect(screen.getByText(sentence(terms.labels.r3_main_done, { count: 1, a: 1, r: 0 }))).toBeVisible();
  expect(screen.getByRole('link', { name: terms.labels.r2_view_draft })).toHaveAttribute(
    'href',
    '/ontology/elements/rule-1?from=%2Fbuild%2Fchangesets%2FCS-1',
  );
  fireEvent.click(submit);
  const dialog = screen.getByRole('dialog');
  expect(dialog).toHaveStyle({
    width: getComputedStyle(document.documentElement).getPropertyValue('--zy-dialog-width'),
  });
  await waitFor(() =>
    expect(within(dialog).getByText(sentence(terms.labels.r3_main_submit_count, { count: 1 }))).toBeVisible(),
  );
  expect(invokeCapability).not.toHaveBeenCalledWith(
    'changeset.apply_and_submit',
    expect.anything(),
    expect.anything(),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: terms.actions.catalog_1_6 }));
  expect(await screen.findByRole('button', { name: terms.labels.r3_main_submitted })).toBeDisabled();
  expect(
    vi.mocked(invokeCapability).mock.calls.filter(([op]) => op === 'changeset.apply_and_submit'),
  ).toHaveLength(1);
});
it('shows the original in place with exact highlight and keeps the selected item after closing', async () => {
  const quote = 'Original';
  proposal.items[0].evidence = [
    {
      material_id: 'M-1',
      anchor_id: 'A-1',
      source_version: 'V-1',
      quote,
      start: 0,
      end: quote.length,
      locator: 'Article 1',
    },
  ] as never;
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.labels.r3_main_view_original }));
  const drawer = screen.getByRole('dialog');
  expect(drawer.querySelector('mark')).toHaveTextContent(quote);
  expect(within(drawer).getByRole('link', { name: terms.labels.r3_main_source_page })).toHaveAttribute(
    'href',
    '/materials/M-1?anchor=A-1&source_version=V-1',
  );
  fireEvent.click(within(drawer).getByRole('button', { name: /关闭|Close/ }));
  expect(screen.getByRole('button', { name: 'Original title' })).toHaveAttribute('aria-current', 'true');
});

it('returns to the exact intake run and treats externally processed candidates as read-only', async () => {
  proposal.items[0].decision = 'processed_elsewhere';
  open();
  expect(await screen.findByRole('link', { name: terms.labels.r2_intake_title })).toHaveAttribute(
    'href',
    '/build/intake?run=run-1',
  );
  expect(screen.getByRole('button', { name: terms.actions.catalog_1_0 })).toBeDisabled();
  expect(screen.getByRole('button', { name: terms.actions.catalog_1_1 })).toBeDisabled();
  expect(screen.getByRole('button', { name: terms.actions.catalog_1_2 })).toBeDisabled();
  expect(screen.getByRole('button', { name: terms.actions.catalog_1_6 })).toBeDisabled();
  expect(screen.getAllByText(terms.labels.r3_main_elsewhere).length).toBeGreaterThan(0);
  expect(screen.queryByText('processed_elsewhere')).not.toBeInTheDocument();
});

it('shows server prerequisites beside the disabled main action instead of claiming already submitted', async () => {
  proposal.items[0].decision = 'accepted';
  proposal.items[0].applied_entity_id = 'rule-1';
  proposal.items[0].gates = gates(['QG4']);
  proposal.submit_blockers = ['请确认规则的适用主体'];
  const { container } = open();
  expect(await screen.findByRole('button', { name: terms.actions.catalog_1_6 })).toBeDisabled();
  expect(screen.getByText('请确认规则的适用主体')).toBeVisible();
  expect(screen.queryByRole('button', { name: terms.labels.r3_main_submitted })).not.toBeInTheDocument();
  expect(container.querySelector('[data-gate-checklist] details')).toHaveAttribute('open');
});
it('saves the missing scope and conditions through the generated candidate patch', async () => {
  proposal.items[0].gates = gates(['QG3']);
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_1_1 }));
  const modal = screen.getByRole('dialog');
  fireEvent.change(within(modal).getByLabelText(terms.labels.r4_rule_scope), {
    target: { value: '合同付款' },
  });
  fireEvent.change(within(modal).getByLabelText(terms.labels.r4_rule_conditions), {
    target: { value: '验收完成\n合同有效' },
  });
  fireEvent.click(within(modal).getByRole('button', { name: terms.actions.r2_save }));
  await waitFor(() =>
    expect(invokeCapability).toHaveBeenCalledWith(
      'changeset.decide_items',
      expect.objectContaining({
        edit: expect.objectContaining({ scope: '合同付款', conditions: ['验收完成', '合同有效'] }),
      }),
      expect.anything(),
    ),
  );
});

it('sends the version shown when editing began, even if a refocus refresh finds a newer draft', async () => {
  proposal.items[0].decision = 'accepted';
  proposal.items[0].applied_entity_id = 'rule-1';
  proposal.can_submit = true;
  Object.assign(proposal.items[0], { applied_version: 3, applied_status: 'draft' });
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_1_1 }));
  const modal = screen.getByRole('dialog');
  Object.assign(proposal.items[0], { applied_version: 4 });
  proposal.items[0].after_data.content = '另一位建设人员刚保存的内容';
  await act(async () => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
  });
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  expect(within(modal).getByLabelText(terms.labels.r2_content)).toHaveValue('Original content');
  fireEvent.click(within(modal).getByRole('button', { name: terms.actions.r2_save }));
  await waitFor(() =>
    expect(invokeCapability).toHaveBeenCalledWith(
      'changeset.decide_items',
      expect.objectContaining({
        edit: expect.objectContaining({ expected_version: 3, content: 'Original content' }),
      }),
      expect.anything(),
    ),
  );
  focusManager.setFocused(undefined);
});

it('requires a separate acceptance after converting content to a rule so its proposed target is visible first', async () => {
  proposal.items[0].after_data.kind = 'knowledge';
  vi.mocked(invokeCapability).mockImplementation(async (operation) => {
    if (operation === 'changeset.decide_items') {
      proposal.items[0].after_data.kind = 'rule';
      proposal.items[0].decision = 'edited';
      Object.assign(proposal.items[0].after_data, {
        applies_to_id: 'org-1',
        applies_to_title: '本机构办公室',
      });
    }
    return {} as never;
  });
  open();
  fireEvent.click(await screen.findByRole('button', { name: terms.actions.catalog_1_1 }));
  const modal = screen.getByRole('dialog');
  fireEvent.mouseDown(within(modal).getByRole('combobox', { name: terms.labels.r2_kind }));
  fireEvent.click(await screen.findByText(terms.kinds.rule, { selector: '.ant-select-item-option-content' }));
  fireEvent.click(within(modal).getByRole('button', { name: terms.labels.r4_rule_save_target }));
  expect(await screen.findByText(terms.labels.r4_rule_target_saved)).toBeVisible();
  expect(screen.getByText(/适用主体或对象：本机构办公室/)).toBeVisible();
  expect(vi.mocked(invokeCapability).mock.calls.filter(([name]) => name === 'changeset.apply')).toHaveLength(
    0,
  );
  fireEvent.click(screen.getByRole('button', { name: terms.actions.catalog_1_0 }));
  await waitFor(() =>
    expect(
      vi.mocked(invokeCapability).mock.calls.filter(([name]) => name === 'changeset.apply'),
    ).toHaveLength(1),
  );
});

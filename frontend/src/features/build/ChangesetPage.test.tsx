import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError, invokeCapability } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import ChangesetPage from './ChangesetPage';

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
  title: 'Policy',
  summary: 'Summary',
  status: 'open',
  source_text: 'Original text',
  source_material_id: 'M-1',
  can_submit: false,
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
    },
  ],
});
let proposal = seed();
beforeEach(() => {
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
  );
}
it('sends only editable fields, shows failure inside the dialog, refreshes and retains the page', async () => {
  open();
  const heading = await screen.findByRole('heading', { name: terms.labels.r2_changeset_title });
  fireEvent.click(screen.getByRole('button', { name: terms.actions.catalog_1_1 }));
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
  expect(await screen.findByRole('button', { name: terms.actions.catalog_1_6 })).toBeDisabled();
});

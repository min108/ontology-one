import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
import ElementPage, { type ElementDetail } from './ElementPage';

vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: vi.fn(),
}));

function detail(): ElementDetail {
  return {
    entity: {
      id: 'rule_private_id',
      kind: 'rule',
      title: 'Payment requirements',
      status: 'published',
      version: 3,
      domain: 'Procurement',
      payload: {
        scope: [
          'Headquarters',
          { name: 'Domestic subsidiaries', value: { description: 'Covered by central payment management' } },
        ],
        conditions: [
          {
            title: 'Before payment',
            content: [{ text: 'Verify the contract' }, { description: 'Confirm acceptance' }],
            candidate_id: 'nested-secret',
          },
        ],
        constraints: {
          unit: 'Ten thousand yuan',
          definition: { content: 'Calculate the tax-inclusive amount', extraction_mode: 'nested-mode' },
          unknown_field: 'unknown-secret',
        },
        obligation: { content: '<script>literal business text</script>' },
        candidate_id: 'candidate-secret',
        source_chunk_ids: ['chunk-secret'],
        extraction_mode: 'mode-secret',
        r2_review: { submitter: 'review-secret' },
        future_internal_field: 'future-secret',
      },
      activities: [
        {
          id: 'a',
          action: 'publish',
          detail: 'Independent review completed',
          created_at: '2026-11-16T18:05:10Z',
        },
        {
          id: 'b',
          action: terms.actions.submit,
          detail: 'Check the original text',
          created_at: '2026-11-17T02:06:00+08:00',
        },
        {
          id: 'c',
          action: 'unknown_internal_action',
          detail: 'internal-action-secret',
          created_at: 'not-a-date',
        },
      ],
    },
    card: {
      one_liner: 'Verify the contract and acceptance records before payment.',
      points: ['Verify the contract'],
      relations: [],
      usage: 'Use for payment review.',
    },
    versions: [
      { id: 'v3', version: 3, created_at: '2026-11-16T18:05:10Z', reason: 'Independent review approved' },
      { id: 'v2', version: 2, created_at: '2026-11-16 18:04:00', change_note: 'Complete the scope' },
      { id: 'v1', version: 1, created_at: 'invalid-secret', reason: 'Create draft' },
    ],
    can_edit: false,
  };
}
async function openElement(value = detail()) {
  vi.mocked(api).mockResolvedValue({ data: value });
  const rendered = render(
    <MemoryRouter initialEntries={['/ontology/elements/rule_private_id']}>
      <Routes>
        <Route path="/ontology/elements/:id" element={<ElementPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByRole('heading', { name: 'Payment requirements' });
  return rendered;
}
beforeEach(() => {
  vi.clearAllMocks();
  useUiStore.setState({ viewMode: 'simple' });
});

it('shows readable nested business information with vocabulary labels and excludes all internal fields', async () => {
  const { container } = await openElement();
  const structure = screen.getByRole('tabpanel', { name: terms.labels.r2_card_structure });
  for (const text of [
    terms.labels.r2_card_field_scope,
    terms.labels.r2_card_field_conditions,
    terms.labels.r2_card_field_obligation,
    terms.labels.r2_card_field_constraints,
    'Headquarters',
    'Domestic subsidiaries',
    'Covered by central payment management',
    'Before payment',
    'Verify the contract',
    'Confirm acceptance',
    `${terms.labels.r2_card_field_unit}：Ten thousand yuan`,
    'Calculate the tax-inclusive amount',
  ]) {
    expect(structure).toHaveTextContent(text);
  }
  expect(structure).toHaveTextContent('<script>literal business text</script>');
  expect(container.querySelector('script')).toBeNull();
  for (const text of [
    'candidate_id',
    'source_chunk_ids',
    'extraction_mode',
    'r2_review',
    'future_internal_field',
    'secret',
    'nested-mode',
    '{',
    '[',
  ]) {
    expect(structure).not.toHaveTextContent(text);
  }
  expect(screen.queryByText(/rule_private_id/)).not.toBeInTheDocument();
  act(() => useUiStore.getState().setViewMode('professional'));
  expect(screen.getByText(/rule_private_id/)).toBeInTheDocument();
  act(() => useUiStore.getState().setViewMode('simple'));
  expect(screen.queryByText(/rule_private_id/)).not.toBeInTheDocument();
});

it('shows the identifier only in professional view while keeping internal payload fields hidden', async () => {
  useUiStore.setState({ viewMode: 'professional' });
  await openElement();
  expect(screen.getByText(/rule_private_id/)).toBeInTheDocument();
  expect(screen.getByRole('tabpanel', { name: terms.labels.r2_card_structure })).not.toHaveTextContent(
    'secret',
  );
});

it('formats versions and activity in Beijing time and translates action identifiers', async () => {
  await openElement();
  fireEvent.click(screen.getByRole('tab', { name: terms.labels.r2_card_versions }));
  const versions = screen.getByRole('tabpanel', { name: terms.labels.r2_card_versions });
  expect(versions).toHaveTextContent('v3 · 2026-11-17 02:05 · Independent review approved');
  expect(versions).toHaveTextContent('v2 · 2026-11-17 02:04 · Complete the scope');
  expect(versions).not.toHaveTextContent('invalid-secret');
  fireEvent.click(screen.getByRole('tab', { name: terms.labels.r2_card_activity }));
  const activity = screen.getByRole('tabpanel', { name: terms.labels.r2_card_activity });
  expect(activity).toHaveTextContent(
    `${terms.actions.catalog_2_4} · Independent review completed · 2026-11-17 02:05`,
  );
  expect(activity).toHaveTextContent(`${terms.actions.submit} · Check the original text · 2026-11-17 02:06`);
  expect(activity).not.toHaveTextContent('publish');
  expect(activity).not.toHaveTextContent('unknown_internal_action');
  expect(activity).not.toHaveTextContent('internal-action-secret');
});

it('shows an empty state when payload contains only metadata or unsupported nested data', async () => {
  const value = detail();
  value.entity.payload = {
    candidate_id: 'secret',
    conditions: { unknown_internal: ['secret'] },
    content: null,
  };
  await openElement(value);
  expect(
    within(screen.getByRole('tabpanel', { name: terms.labels.r2_card_structure })).getByText(
      terms.labels.r2_card_empty,
    ),
  ).toBeInTheDocument();
});

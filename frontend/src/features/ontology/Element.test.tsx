import sourceLocations from '../../../test-fixtures/source-locations.json';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { sentence } from '../build/model';
import { gates } from '../build/gateFixtures';
import { useUiStore } from '../../state/ui';
import { useSessionStore } from '../../state/session';
import { testQuery, testSession } from '../build/testQuery';
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
async function openElement(value = detail(), path = '/ontology/elements/rule_private_id') {
  vi.mocked(api).mockResolvedValue({ data: value });
  const rendered = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/ontology/elements/:id" element={<ElementPage />} />
      </Routes>
    </MemoryRouter>,
    { wrapper: testQuery() },
  );
  await screen.findByRole('heading', { name: 'Payment requirements' });
  return rendered;
}
beforeEach(() => {
  testSession();
  vi.clearAllMocks();
  useUiStore.setState({ viewMode: 'simple' });
});

it('shows readable nested business information with vocabulary labels and excludes all internal fields', async () => {
  const { container } = await openElement();
  expect(screen.getByText('Use for payment review.')).toBeVisible();
  expect(screen.queryByRole('region', { name: terms.labels.r2_card_structure })).not.toBeInTheDocument();
  expect(screen.queryByText(/rule_private_id/)).not.toBeInTheDocument();
  act(() => useUiStore.getState().setViewMode('professional'));
  const structure = screen.getByRole('region', { name: terms.labels.r2_card_structure });
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
  expect(screen.getByText(/rule_private_id/)).not.toBeVisible();
  fireEvent.click(screen.getByText(terms.labels.r3_ui_technical));
  expect(screen.getByText(/rule_private_id/)).toBeVisible();
  act(() => useUiStore.getState().setViewMode('simple'));
  expect(screen.queryByText(/rule_private_id/)).not.toBeInTheDocument();
});

it('shows the identifier only in professional view while keeping internal payload fields hidden', async () => {
  useUiStore.setState({ viewMode: 'professional' });
  await openElement();
  expect(screen.getByText(/rule_private_id/)).not.toBeVisible();
  fireEvent.click(screen.getByText(terms.labels.r3_ui_technical));
  expect(screen.getByText(/rule_private_id/)).toBeVisible();
  expect(screen.getByRole('region', { name: terms.labels.r2_card_structure })).not.toHaveTextContent(
    'secret',
  );
});

it('formats versions and activity in Beijing time and translates action identifiers', async () => {
  await openElement();
  fireEvent.click(screen.getByText(terms.labels.r2_card_versions));
  const versions = screen.getByRole('region', { name: terms.labels.r2_card_versions });
  expect(versions).toHaveTextContent('v3 · 2026-11-17 02:05 · Independent review approved');
  expect(versions).toHaveTextContent('v2 · 2026-11-17 02:04 · Complete the scope');
  expect(versions).not.toHaveTextContent('invalid-secret');
  fireEvent.click(screen.getByText(terms.labels.r2_card_activity));
  const activity = screen.getByRole('region', { name: terms.labels.r2_card_activity });
  expect(activity).toHaveTextContent(
    `${terms.actions.catalog_2_4} · Independent review completed · 2026-11-17 02:05`,
  );
  expect(activity).toHaveTextContent(`${terms.actions.submit} · Check the original text · 2026-11-17 02:06`);
  expect(activity).not.toHaveTextContent('publish');
  expect(activity).not.toHaveTextContent('unknown_internal_action');
  expect(activity).not.toHaveTextContent('internal-action-secret');
});

it('hides empty folds when payload contains only metadata or unsupported nested data', async () => {
  const value = detail();
  value.entity.payload = {
    candidate_id: 'secret',
    conditions: { unknown_internal: ['secret'] },
    content: null,
  };
  useUiStore.setState({ viewMode: 'professional' });
  await openElement(value);
  expect(screen.queryByRole('region', { name: terms.labels.r2_card_structure })).not.toBeInTheDocument();
  expect(screen.queryByText(terms.labels.r3_main_conditions)).not.toBeInTheDocument();
  expect(screen.queryByText(terms.labels.r2_card_relations)).not.toBeInTheDocument();
  expect(screen.getByText(/unknown_internal/)).not.toBeVisible();
});

it('renders a draft in the new archive page without a published question action', async () => {
  const value = detail();
  value.entity.status = 'draft';
  value.can_edit = true;
  await openElement(value);
  expect(screen.getByText(terms.statuses.element.draft)).toBeVisible();
  expect(screen.getByRole('button', { name: terms.labels.r3_main_edit_draft })).toBeEnabled();
  expect(screen.queryByRole('button', { name: terms.labels.r3_main_ask })).not.toBeInTheDocument();
  expect(screen.getByText(terms.labels.r3_main_draft_note)).toBeVisible();
});

it('returns a mainline draft to its construction proposal and offers completed-step navigation', async () => {
  const value = detail();
  value.entity.status = 'draft';
  value.can_edit = true;
  await openElement(value, '/ontology/elements/rule_private_id?from=%2Fbuild%2Fchangesets%2Fcs-1');
  expect(screen.getByRole('link', { name: terms.labels.r2_changeset_title })).toHaveAttribute(
    'href',
    '/build/changesets/cs-1',
  );
  const steps = screen.getByRole('navigation', { name: terms.labels.r3_ui_next });
  expect(within(steps).getByRole('button', { name: terms.actions.r3_ui_step1 })).toBeEnabled();
  expect(screen.queryByRole('heading', { name: terms.labels.r3_main_next })).not.toBeInTheDocument();
});

it('shows each source once, counts conditions, and keeps positions inside technical details', async () => {
  const value = detail();
  value.entity.payload.source_refs = [
    {
      material_id: 'm',
      anchor_id: 'a',
      source_version: 'v',
      start: 0,
      end: 6,
      title: 'Payment policy',
      quote: 'Review payment',
      locator: sourceLocations.characterRange,
    },
    { excerpt: 'Historical policy excerpt' },
  ];
  value.versions = [];
  value.entity.activities = [];
  const { container } = await openElement(value);
  expect(screen.getAllByText('Payment policy · Review payment')).toHaveLength(1);
  expect(screen.getByRole('button', { name: terms.labels.r3_main_view_original })).toBeVisible();
  expect(screen.getByText(terms.labels.r3_main_conditions).closest('summary')).toHaveTextContent(
    sentence(terms.labels.r3_main_count, { count: 3 }),
  );
  expect(screen.getByText(terms.labels.r31_other_sources).closest('summary')).toHaveTextContent(
    sentence(terms.labels.r3_main_count, { count: 1 }),
  );
  expect(screen.queryByText(terms.labels.r2_card_versions)).not.toBeInTheDocument();
  expect(screen.queryByText(terms.labels.r2_card_activity)).not.toBeInTheDocument();
  expect(container).not.toHaveTextContent(/原文第|阅读版/);
  fireEvent.click(screen.getByText(terms.labels.r31_other_sources));
  expect(screen.getByText('Historical policy excerpt')).toBeVisible();
  act(() => useUiStore.setState({ viewMode: 'professional' }));
  expect(container.querySelector('[data-technical-details]')).toHaveTextContent(
    sourceLocations.characterRange,
  );
});

it.each([
  ['viewer', '', 'citing_entity_id=rule_private_id'],
  ['analyst', 'REL-7', 'citing_relation_id=REL-7'],
] as const)(
  'opens a published citation with its actual subject for a %s',
  async (role, relation, subject) => {
    useSessionStore.setState(({ session }) => ({
      session: { ...session!, user: { ...session!.user, role } },
    }));
    const value = detail();
    const source = {
      material_id: 'M',
      source_version: 'OLD',
      anchor_id: 'A',
      quote: 'Former wording',
      ...(relation ? { citing_relation_id: relation } : {}),
    };
    value.entity.payload.source_refs = [source];
    value.gates = gates();
    value.gates.fields = [
      {
        ...source,
        key: 'scope',
        label: 'Scope',
        source: 'source',
        value: 'Branch',
        source_quote: source.quote,
      },
    ];
    await openElement(value);
    const href = '/materials/M?anchor=A&source_version=OLD&' + subject;
    expect(screen.getByRole('link', { name: source.quote })).toHaveAttribute('href', href);
    const text = '📄' + 'a'.repeat(4095);
    vi.mocked(api).mockResolvedValueOnce({
      material_id: 'M',
      title: 'Cited material',
      source_version: 'OLD',
      status: 'published',
      version: 1,
      locator: 'Article 2',
      text,
      offset: 0,
      total: 5600,
    });
    vi.mocked(api).mockRejectedValueOnce(new ApiError('Original is no longer available', 404));
    fireEvent.click(screen.getByRole('button', { name: terms.labels.r3_main_view_original }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByRole('link', { name: terms.labels.r3_main_source_page })).toHaveAttribute(
      'href',
      href,
    );
    expect(await within(drawer).findByText(text)).toBeVisible();
    expect(vi.mocked(api).mock.calls.at(-1)?.[0]).toBe(
      '/v2/materials/M/source?offset=0&anchor_id=A&source_version=OLD&' + subject,
    );
    fireEvent.click(within(drawer).getByRole('button', { name: terms.actions.identity_next }));
    expect(await within(drawer).findByText('Original is no longer available')).toBeVisible();
    expect(drawer.querySelector('[data-source-viewer]')).toBeNull();
    expect(within(drawer).queryByText(source.quote)).toBeNull();
    expect(vi.mocked(api).mock.calls.at(-1)?.[0]).toBe(
      '/v2/materials/M/source?offset=4096&anchor_id=A&source_version=OLD&' + subject,
    );
  },
);
it('uses the catalog return for a direct archive visit and ignores arbitrary from destinations', async () => {
  await openElement(detail(), '/ontology/elements/rule_private_id?from=https%3A%2F%2Fexample.invalid');
  expect(screen.getByRole('link', { name: terms.actions.r2_card_back })).toHaveAttribute(
    'href',
    '/ontology/catalog',
  );
  expect(screen.queryByRole('navigation', { name: terms.labels.r3_ui_next })).not.toBeInTheDocument();
});

it('carries known extraction origin into published and draft business cards', async () => {
  const value = detail();
  value.entity.payload.extraction_mode = 'online';
  await openElement(value);
  expect(screen.getByText(terms.labels.r3_ui_ai_reviewed)).toBeVisible();
  expect(screen.queryByText('online')).not.toBeInTheDocument();
});

it('focuses the repair panel after a legacy repair link loads its editable draft', async () => {
  const value = detail();
  value.can_edit = true;
  value.entity.status = 'draft';
  value.gates = gates(['QG2']);
  const scroll = vi.fn();
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: scroll });
  try {
    await openElement(value, '/ontology/elements/rule_private_id?repair=1');
    const panel = screen.getByRole('region', { name: terms.labels.r4a1_legacy_repair_title });
    expect(panel).toHaveFocus();
    expect(scroll.mock.instances).toContain(panel);
  } finally {
    Reflect.deleteProperty(Element.prototype, 'scrollIntoView');
  }
});

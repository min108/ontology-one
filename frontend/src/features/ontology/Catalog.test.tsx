import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { BusinessCard, type ElementDetail } from './ElementPage';
import { testQuery, testSession } from '../build/testQuery';
import { api } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import CatalogPage, { assetKinds } from './CatalogPage';
import { useUiStore } from '../../state/ui';

vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  api: vi.fn(),
}));
it('presents the four kinds and a literal, anchored business card', async () => {
  testSession();
  expect(assetKinds).toEqual(['data', 'rule', 'skill', 'knowledge']);
  const detail: ElementDetail = {
    entity: {
      id: 'r',
      kind: 'rule',
      title: 'Payment rule',
      status: 'published',
      version: 4,
      domain: 'Procurement',
      activities: [],
      payload: {
        source_refs: [
          {
            material_id: 'M-1',
            anchor_id: 'A-1',
            source_version: 'V-1',
            quote: 'Pay within 30 days.',
            locator: 'Article 1',
          },
        ],
      },
    },
    card: {
      one_liner: '<script>literal</script>',
      points: ['Verify the agreement'],
      relations: [],
      usage: 'Check the source.',
    },
    versions: [],
    can_edit: false,
  };
  const { container } = render(
    <MemoryRouter>
      <BusinessCard detail={detail} />
    </MemoryRouter>,
    { wrapper: testQuery() },
  );
  expect(screen.getByText('<script>literal</script>')).toBeInTheDocument();
  expect(container.querySelector('script')).toBeNull();
  vi.mocked(api).mockResolvedValue({
    text: 'Pay within 30 days.',
    focus: { start: 0, end: 19 },
    source_version: 'V-1',
  });
  expect(screen.getAllByText('Article 1 · Pay within 30 days.')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r3_main_view_original }));
  const drawer = await screen.findByRole('dialog');
  expect(within(drawer).getByRole('link', { name: terms.labels.r3_main_source_page })).toHaveAttribute(
    'href',
    '/materials/M-1?anchor=A-1&source_version=V-1',
  );
  expect(drawer.querySelector('mark')).toHaveTextContent('Pay within 30 days.');
});

function CatalogLocation() {
  const location = useLocation();
  return <output data-testid="catalog-location">{location.pathname + location.search}</output>;
}
it('searches the catalog through the server, preserves six simple columns and adds version in professional view', async () => {
  testSession();
  useUiStore.setState({ viewMode: 'simple' });
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(
    async (path) =>
      ({
        data: {
          items: [
            {
              id: 'rule_internal_secret',
              title: path.includes('q=') && !path.includes('q=&') ? 'Filtered payment rule' : 'Payment rule',
              kind: 'rule',
              status: 'published',
              source_title: 'Payment policy',
              updated_at: '2026-11-17T01:30:00Z',
              version: 3,
            },
          ],
          total: 26,
        },
      }) as never,
  );
  render(
    <MemoryRouter initialEntries={['/ontology/catalog']}>
      <CatalogPage />
      <CatalogLocation />
    </MemoryRouter>,
    { wrapper: testQuery() },
  );
  expect(await screen.findByRole('link', { name: 'Payment rule' })).toHaveAttribute(
    'href',
    '/ontology/elements/rule_internal_secret',
  );
  expect(screen.getAllByRole('columnheader')).toHaveLength(6);
  expect(screen.getByText('Payment policy')).toBeVisible();
  expect(screen.getByText('2026-11-17 09:30')).toBeVisible();
  expect(screen.queryByText('rule_internal_secret')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: terms.actions.columns })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(terms.labels.r2_catalog_search), { target: { value: 'payment' } });
  fireEvent.keyDown(screen.getByLabelText(terms.labels.r2_catalog_search), { key: 'Enter', code: 'Enter' });
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith(expect.stringContaining('q=payment'), expect.anything()),
  );
  expect(await screen.findByRole('link', { name: 'Filtered payment rule' })).toBeVisible();
  act(() => useUiStore.getState().setViewMode('professional'));
  expect(screen.getAllByRole('columnheader')).toHaveLength(7);
  expect(screen.getByText('v3')).toBeVisible();
  expect(screen.getByRole('button', { name: terms.actions.columns })).toBeEnabled();
  act(() => useUiStore.getState().setViewMode('simple'));
  expect(screen.getAllByRole('columnheader')).toHaveLength(6);
  expect(screen.queryByText('v3')).not.toBeInTheDocument();
});
it('hides headings and pagination for an empty catalog and can clear the search', async () => {
  testSession();
  useUiStore.setState({ viewMode: 'simple' });
  vi.mocked(api).mockResolvedValue({ data: { items: [], total: 0 } });
  render(
    <MemoryRouter initialEntries={['/ontology/catalog?q=missing']}>
      <CatalogPage />
      <CatalogLocation />
    </MemoryRouter>,
    { wrapper: testQuery() },
  );
  expect(await screen.findByText(terms.labels.r3_main_catalog_none)).toBeVisible();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.queryByRole('columnheader')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: terms.labels.r3_main_catalog_clear }));
  expect(screen.getByTestId('catalog-location')).toHaveTextContent('/ontology/catalog?q=&kind=&page=1');
});

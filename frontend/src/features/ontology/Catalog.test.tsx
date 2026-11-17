import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { BusinessCard, type ElementDetail } from './ElementPage';
import { testQuery, testSession } from '../build/testQuery';
import { api } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { assetKinds } from './CatalogPage';

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
  fireEvent.click(screen.getByRole('button', { name: 'Article 1' }));
  const drawer = await screen.findByRole('dialog');
  expect(within(drawer).getByRole('link', { name: terms.labels.r3_main_source_page })).toHaveAttribute(
    'href',
    '/materials/M-1?anchor=A-1&source_version=V-1',
  );
  expect(drawer.querySelector('mark')).toHaveTextContent('Pay within 30 days.');
});

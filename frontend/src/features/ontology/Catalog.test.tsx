import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it } from 'vitest';
import { BusinessCard, type ElementDetail } from './ElementPage';
import { assetKinds } from './CatalogPage';

it('presents the four kinds and a literal, anchored business card', () => {
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
  );
  expect(screen.getByText('<script>literal</script>')).toBeInTheDocument();
  expect(container.querySelector('script')).toBeNull();
  expect(screen.getByRole('link', { name: 'Article 1' })).toHaveAttribute(
    'href',
    '/materials/M-1?anchor=A-1&source_version=V-1',
  );
  expect(screen.getByText('Check the source.')).toBeInTheDocument();
});

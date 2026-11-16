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
      title: '付款规则',
      status: 'published',
      version: 4,
      domain: '采购',
      activities: [],
      payload: {
        source_refs: [
          {
            material_id: 'M-1',
            anchor_id: 'A-1',
            source_version: 'V-1',
            quote: '30日内付款。',
            locator: '第一条',
          },
        ],
      },
    },
    card: {
      one_liner: '<script>literal</script>',
      points: ['要求：先核对'],
      relations: [],
      usage: '核对原文。',
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
  expect(screen.getByRole('link', { name: '第一条' })).toHaveAttribute(
    'href',
    '/materials/M-1?anchor=A-1&source_version=V-1',
  );
  expect(screen.getByText('核对原文。')).toBeInTheDocument();
});

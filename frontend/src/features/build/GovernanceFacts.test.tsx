import legacyFixture from '../../../test-fixtures/r4a1-legacy-content.json';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it } from 'vitest';
import { GovernanceFacts } from './GovernanceFacts';
import { gates } from './gateFixtures';
import { terms } from '../../i18n/zh-CN';

it('shows actual effective date, provenance, evidence and applicable subject link', () => {
  const result = gates();
  result.fields = [
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
    {
      key: 'subject:org',
      label: legacyFixture.subjectLabel,
      value: legacyFixture.subjectValue,
      source: 'editor',
      source_quote: '',
      confirmed_by: '',
      href: '/assets/org',
    },
  ];
  const { rerender } = render(
    <MemoryRouter>
      <GovernanceFacts gates={result} />
    </MemoryRouter>,
  );
  expect(screen.getByText('2026-01-01')).toBeVisible();
  expect(screen.getByText(legacyFixture.dateQuote)).toBeVisible();
  expect(screen.getByText(terms.labels.r4a1_legacy_origin_source)).toBeVisible();
  expect(screen.getByText(/批量确认/)).toHaveTextContent(legacyFixture.reviewer);
  expect(screen.getByRole('link', { name: legacyFixture.subjectValue })).toHaveAttribute(
    'href',
    '/assets/org',
  );
  // Counterexample: absent date cannot retain the earlier real value or claim source extraction.
  result.fields = [{ ...result.fields[0], value: '', source: 'missing', source_quote: '' }];
  rerender(
    <MemoryRouter>
      <GovernanceFacts gates={result} />
    </MemoryRouter>,
  );
  expect(screen.queryByText('2026-01-01')).not.toBeInTheDocument();
  expect(screen.queryByText(terms.labels.r4a1_legacy_origin_source)).not.toBeInTheDocument();
  expect(screen.getByText(terms.labels.r4a1_legacy_origin_missing, { selector: 'p' })).toBeVisible();
});

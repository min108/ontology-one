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
      label: '生效日期',
      value: '2026-01-01',
      source: 'source',
      source_quote: '本规定自2026年1月1日起施行。',
      confirmed_by: '',
      href: '',
    },
    {
      key: 'exceptions',
      label: '例外',
      value: '无其他例外',
      source: 'batch_confirmation',
      source_quote: '',
      confirmed_by: '核对人员',
      href: '',
    },
    {
      key: 'subject:org',
      label: '适用主体或对象',
      value: '采购部',
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
  expect(screen.getByText('本规定自2026年1月1日起施行。')).toBeVisible();
  expect(screen.getByText(terms.labels.r4a1_legacy_origin_source)).toBeVisible();
  expect(screen.getByText(/批量确认/)).toHaveTextContent('核对人员');
  expect(screen.getByRole('link', { name: '采购部' })).toHaveAttribute('href', '/assets/org');
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

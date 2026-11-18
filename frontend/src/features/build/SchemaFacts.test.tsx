import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it } from 'vitest';
import fixture from '../../../test-fixtures/r4a1-schema-facts.json';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
import { SchemaFacts } from './SchemaFacts';

beforeEach(() => useUiStore.setState({ viewMode: 'simple' }));
afterEach(() => useUiStore.setState({ viewMode: 'simple' }));
it('renders actual Chinese fields, type, requirement and bounds without exposing raw schema in simple mode', () => {
  const { container, rerender } = render(
    <MemoryRouter>
      <SchemaFacts value={JSON.stringify(fixture.inputs)} direction="inputs" />
    </MemoryRouter>,
  );
  expect(screen.getByText(fixture.amountLabel)).toBeVisible();
  expect(container).toHaveTextContent(terms.labels.r4a1_schema_number);
  expect(container).toHaveTextContent(terms.labels.r4a1_schema_required);
  expect(container).toHaveTextContent('0');
  expect(container).toHaveTextContent('100');
  expect(container.textContent).not.toMatch(/properties|additionalProperties|required|"type"|\{"/);
  expect(container.querySelector('pre')).toBeNull();
  rerender(
    <MemoryRouter>
      <SchemaFacts value={JSON.stringify({ ...fixture.inputs, required: [] })} direction="inputs" />
    </MemoryRouter>,
  );
  expect(container).not.toHaveTextContent(terms.labels.r4a1_schema_required);
  expect(container).toHaveTextContent(terms.labels.r4a1_schema_optional);
});
it('keeps the exact original structure behind the professional technical fold', () => {
  useUiStore.setState({ viewMode: 'professional' });
  const raw = JSON.stringify(fixture.outputs);
  const { container } = render(
    <MemoryRouter>
      <SchemaFacts value={raw} direction="outputs" />
    </MemoryRouter>,
  );
  const code = container.querySelector('pre');
  expect(code).toHaveTextContent(raw);
  expect(code).not.toBeVisible();
  fireEvent.click(screen.getByText(terms.labels.r3_ui_technical));
  expect(code).toBeVisible();
  expect(screen.getByText(fixture.resultLabel)).toBeVisible();
});
it('does not leak malformed structured text or invent missing fields', () => {
  const raw = '{"properties":';
  const { container } = render(
    <MemoryRouter>
      <SchemaFacts value={raw} direction="inputs" />
    </MemoryRouter>,
  );
  expect(screen.getByText(terms.labels.r4a1_schema_pending)).toBeVisible();
  expect(container).not.toHaveTextContent(raw);
  expect(screen.queryByText(fixture.amountLabel)).not.toBeInTheDocument();
});
it('preserves an existing plain business input declaration', () => {
  render(
    <MemoryRouter>
      <SchemaFacts value={fixture.amountLabel} direction="inputs" />
    </MemoryRouter>,
  );
  expect(screen.getByText(fixture.amountLabel)).toBeVisible();
  expect(screen.queryByText(terms.labels.r4a1_schema_pending)).not.toBeInTheDocument();
});

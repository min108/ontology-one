import legacyFixture from '../../../test-fixtures/r4a1-legacy-content.json';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { invokeCapability } from '../../api/client';
import type { ElementDetail } from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { gates } from '../build/gateFixtures';
import { ElementRepair } from './ElementRepair';

vi.mock('../../api/client', () => ({ invokeCapability: vi.fn() }));
const copy = terms.labels;
function detail(): ElementDetail {
  const checks = gates(['QG1', 'QG2']);
  checks.ready_to_submit = false;
  checks.fields = [
    {
      key: 'definition',
      label: legacyFixture.definitionLabel,
      value: '',
      source: 'missing',
      source_quote: '',
      confirmed_by: '',
      href: '',
    },
  ];
  return {
    entity: {
      id: 'data-1',
      kind: 'data',
      title: legacyFixture.dataTitle,
      summary: '',
      domain: '',
      status: 'draft',
      version: 2,
      activities: [],
      payload: {
        unit: legacyFixture.unit,
        grain: legacyFixture.grain,
        historical_note: legacyFixture.historicalNote,
      },
    },
    card: { one_liner: '', points: [], relations: [], usage: '' },
    versions: [],
    can_edit: true,
    gates: checks,
  };
}
beforeEach(() => vi.clearAllMocks());
it('keeps every authority subject visible, replaces only the changed set and excludes unsupported kinds', async () => {
  const current = detail();
  current.entity.kind = 'rule';
  current.entity.payload.authority_spec = { org_ids: ['org-a', 'org-b'], person_ids: ['person-a'] };
  current.applicability_options = [
    { id: 'org-a', title: 'Department A', kind: 'organization' },
    { id: 'org-b', title: 'Department B', kind: 'organization' },
    { id: 'person-a', title: 'Reviewer A', kind: 'person' },
    { id: 'object-a', title: 'Business object', kind: 'object_type' },
    { id: 'position-a', title: 'Position A', kind: 'position' },
  ];
  const onSaved = vi.fn();
  const { container } = render(
    <MemoryRouter>
      <ElementRepair detail={current} disabled={false} onSaved={onSaved} />
    </MemoryRouter>,
  );
  for (const title of ['Department A', 'Department B', 'Reviewer A'])
    expect(container.querySelector(`.ant-select-selection-item[title="${title}"]`)).not.toBeNull();
  fireEvent.click(screen.getByRole('button', { name: copy.r4a1_legacy_save }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
  expect(vi.mocked(invokeCapability).mock.calls[0][1]).toEqual(expect.objectContaining({ payload: {} }));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: copy.r4a1_legacy_subject }));
  expect(screen.queryByTitle('Business object')).not.toBeInTheDocument();
  expect(screen.queryByTitle('Position A')).not.toBeInTheDocument();
  fireEvent.click(
    container.querySelector(
      '.ant-select-selection-item[title="Department A"] .ant-select-selection-item-remove',
    )!,
  );
  fireEvent.click(screen.getByRole('button', { name: copy.r4a1_legacy_save }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(2));
  expect(vi.mocked(invokeCapability).mock.calls[1][1]).toEqual(
    expect.objectContaining({ payload: { applicable_subject_ids: ['org-b', 'person-a'] } }),
  );
  expect(container.querySelector('.ant-select-selection-item[title="Department A"]')).toBeNull();
  expect(container.querySelectorAll('.ant-select-selection-item')).toHaveLength(2);
});
it('explains and blocks empty or person-only authority scope, preserving selected Chinese names', async () => {
  const current = detail();
  current.entity.kind = 'rule';
  current.entity.payload.authority_spec = { org_ids: ['org-a'], person_ids: ['person-a'] };
  current.applicability_options = [
    { id: 'org-a', title: legacyFixture.subjectValue, kind: 'organization' },
    { id: 'person-a', title: legacyFixture.reviewer, kind: 'person' },
  ];
  const { container } = render(
    <MemoryRouter>
      <ElementRepair detail={current} disabled={false} onSaved={vi.fn()} />
    </MemoryRouter>,
  );
  const save = screen.getByRole('button', { name: copy.r4a1_legacy_save });
  expect(save).toBeEnabled();
  const remove = (title: string) =>
    fireEvent.click(
      container.querySelector(
        `.ant-select-selection-item[title="${title}"] .ant-select-selection-item-remove`,
      )!,
    );
  remove(legacyFixture.subjectValue);
  expect(screen.getByRole('alert')).toHaveTextContent(copy.r4a1_authority_subject_required);
  expect(
    container.querySelector(`.ant-select-selection-item[title="${legacyFixture.reviewer}"]`),
  ).not.toBeNull();
  expect(save).toBeDisabled();
  fireEvent.click(save);
  expect(invokeCapability).not.toHaveBeenCalled();
  remove(legacyFixture.reviewer);
  expect(save).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent(copy.r4a1_authority_subject_required);
  fireEvent.mouseDown(screen.getByRole('combobox', { name: copy.r4a1_legacy_subject }));
  fireEvent.click(screen.getByTitle(legacyFixture.subjectValue));
  expect(save).toBeEnabled();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  fireEvent.click(save);
  await waitFor(() => expect(invokeCapability).toHaveBeenCalledTimes(1));
  expect(vi.mocked(invokeCapability).mock.calls[0][1]).toEqual(
    expect.objectContaining({ payload: { applicable_subject_ids: ['org-a'] } }),
  );
});
it('uses the shared calendar and saves the selected date as metadata, never as payload', async () => {
  const current = detail();
  current.gates!.fields!.push({
    key: 'valid_from',
    label: legacyFixture.dateLabel,
    value: '',
    source: 'missing',
    source_quote: '',
    confirmed_by: '',
    href: '',
  });
  const onSaved = vi.fn();
  const { container } = render(
    <MemoryRouter>
      <ElementRepair detail={current} disabled={false} onSaved={onSaved} />
    </MemoryRouter>,
  );
  const date = screen.getByLabelText(legacyFixture.dateLabel);
  expect(date).toHaveValue('');
  expect(container.querySelector('input[type="date"]')).toBeNull();
  expect(date.closest('.ant-picker')).not.toBeNull();
  fireEvent.focus(date);
  fireEvent.change(date, { target: { value: '2026-01-01' } });
  fireEvent.keyDown(date, { key: 'Enter', code: 'Enter' });
  fireEvent.blur(date);
  fireEvent.click(screen.getByRole('button', { name: copy.r4a1_legacy_save }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(invokeCapability).toHaveBeenCalledWith(
    'element.update_draft',
    expect.objectContaining({
      meta: { valid_from: '2026-01-01' },
      payload: current.entity.payload,
    }),
    expect.anything(),
  );
});
it('keeps a missing definition non-waivable and requires saving the exact application before submit', async () => {
  const current = detail(),
    onSaved = vi.fn();
  const { container } = render(
    <MemoryRouter>
      <ElementRepair detail={current} disabled={false} onSaved={onSaved} />
    </MemoryRouter>,
  );
  expect(screen.getByRole('button', { name: copy.r4a1_legacy_submit })).toBeDisabled();
  expect(container.querySelector('[data-waiver-application-code="QG2"]')).toBeNull();
  fireEvent.change(screen.getByLabelText(legacyFixture.definitionLabel), {
    target: { value: legacyFixture.definition },
  });
  fireEvent.change(
    screen.getByLabelText(current.gates!.checks[0].label + ' · ' + copy.r4a1_legacy_application),
    { target: { value: legacyFixture.manualApplication } },
  );
  fireEvent.click(screen.getByRole('button', { name: copy.r4a1_legacy_save }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(copy.r4a1_legacy_saved));
  expect(invokeCapability).toHaveBeenCalledWith(
    'element.update_draft',
    expect.objectContaining({
      version: 2,
      payload: { ...current.entity.payload, definition: legacyFixture.definition },
      waiver_applications: { QG1: legacyFixture.manualApplication },
    }),
    expect.anything(),
  );
  expect(vi.mocked(invokeCapability).mock.calls.some(([name]) => name === 'element.submit_draft')).toBe(
    false,
  );
});
it('uses current server submission permission, and unsaved changes cannot submit', async () => {
  const current = detail();
  current.gates!.ready_to_submit = true;
  const { rerender } = render(
    <MemoryRouter>
      <ElementRepair detail={current} disabled={false} onSaved={() => undefined} />
    </MemoryRouter>,
  );
  expect(screen.getByRole('button', { name: copy.r4a1_legacy_submit })).toBeEnabled();
  fireEvent.change(screen.getByLabelText(legacyFixture.definitionLabel), {
    target: { value: legacyFixture.unsavedDefinition },
  });
  expect(screen.getByRole('button', { name: copy.r4a1_legacy_submit })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: copy.r4a1_legacy_submit }));
  expect(invokeCapability).not.toHaveBeenCalled();
  rerender(
    <MemoryRouter>
      <ElementRepair key="fresh" detail={current} disabled onSaved={() => undefined} />
    </MemoryRouter>,
  );
  expect(screen.getByRole('button', { name: copy.r4a1_legacy_submit })).toBeDisabled();
});

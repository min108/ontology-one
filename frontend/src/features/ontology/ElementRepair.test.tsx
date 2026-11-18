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

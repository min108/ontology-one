import reviewFixture from '../../../test-fixtures/r4a2-source-review.json';
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import type { Proposal, UnformedSource } from '../../api/generated/types';
import { ChangesetUnformedSources } from './ChangesetUnformedSources';
import { copy } from './shared';

const original: UnformedSource = {
  material_id: 'material',
  anchor_id: 'anchor',
  source_version: 'source',
  unformed_reason: 'invalid_quote',
  quote: reviewFixture.unformedParagraph,
  locator: reviewFixture.paragraphLocator,
};
it('shows the original, preserves only its version and anchor, and disables read-only writes', () => {
  const retain = vi.fn();
  const proposal: Proposal = {
    id: 'proposal',
    title: reviewFixture.materialTitle,
    summary: '',
    status: 'open',
    source_text: original.quote,
    source_material_id: original.material_id,
    can_submit: false,
    items: [],
    unformed_sources: [original],
  };
  const renderSources = (disabled: boolean) => (
    <MemoryRouter>
      <ChangesetUnformedSources proposal={proposal} disabled={disabled} onRetain={retain} />
    </MemoryRouter>
  );
  const { rerender } = render(renderSources(true));
  expect(screen.getByText(original.quote)).toBeVisible();
  expect(screen.getByRole('link', { name: original.locator })).toHaveAttribute(
    'href',
    '/materials/material?anchor=anchor&source_version=source',
  );
  const keep = screen.getByRole('button', { name: copy.r4a2_keep_source });
  expect(keep).toBeDisabled();
  fireEvent.click(keep);
  expect(retain).not.toHaveBeenCalled();
  rerender(renderSources(false));
  fireEvent.click(keep);
  expect(retain).toHaveBeenCalledExactlyOnceWith({
    changeset_id: 'proposal',
    anchor_id: 'anchor',
    source_version: 'source',
  });
});

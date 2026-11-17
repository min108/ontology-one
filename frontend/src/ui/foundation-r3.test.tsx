import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PageLayout } from './PageLayout';
import { PageHeader } from './PageHeader';
import { FoldRow } from './FoldRow';
import { SourceViewer } from './SourceViewer';
import { RowActions } from './RowActions';
import { formatDateTime } from './dateTime';
import { terms } from '../i18n/zh-CN';

describe('R3 page and evidence contracts', () => {
  it('keeps a disabled main action and its explanation in the title region', () => {
    render(
      <PageLayout
        header={
          <PageHeader
            title="Review"
            note="Two decisions remain"
            primaryAction={{ label: 'Submit', disabled: true, onClick: vi.fn() }}
          />
        }
      >
        <p>Saved content</p>
      </PageLayout>,
    );
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled();
    expect(screen.getByText('Two decisions remain').closest('[data-page-header]')).not.toBeNull();
    expect(screen.getByText('Saved content').closest('[data-page-content]')).not.toBeNull();
  });
  it('does not show a return path on a list; puts an object return inside its header', () => {
    const view = render(<PageHeader title="Catalog" />);
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
    view.rerender(<PageHeader title="Element" back={{ label: 'Catalog', href: '/ontology/catalog' }} />);
    expect(screen.getByRole('link', { name: 'Catalog' }).closest('[data-page-header]')).not.toBeNull();
  });
  it('only highlights an exact quote at the supplied Unicode codepoint offsets', () => {
    const view = render(<SourceViewer text="x😀abc end" quote="abc" start={2} end={5} />);
    expect(view.container.querySelector('mark')).toHaveTextContent('abc');
    view.rerender(<SourceViewer text="x😀abc end" quote="different" start={2} end={5} />);
    expect(view.container.querySelector('mark')).toBeNull();
    expect(screen.getByText('x😀abc end')).toBeInTheDocument();
  });
  it('opens a counted disclosure without losing its body', () => {
    render(
      <FoldRow title="Relations" summary="3 items">
        <p>Related content</p>
      </FoldRow>,
    );
    const summary = screen.getByText('Relations').closest('summary')!;
    expect(summary).toHaveTextContent('3 items');
    fireEvent.click(summary);
    expect(summary.parentElement).toHaveAttribute('open');
    expect(screen.getByText('Related content')).toBeInTheDocument();
  });
  it('limits row actions to two visible buttons and executes the overflow action', async () => {
    const action = vi.fn();
    render(
      <RowActions
        actions={[
          { label: 'Edit', onClick: vi.fn() },
          { label: 'Reset', onClick: vi.fn() },
          { label: 'Disable', onClick: action },
        ]}
      />,
    );
    expect(screen.getAllByRole('button')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: terms.actions.r3_ui_more }));
    fireEvent.click(await screen.findByText('Disable'));
    expect(action).toHaveBeenCalledOnce();
  });
  it('formats UTC timestamps in Beijing time and hides invalid timestamps', () => {
    expect(formatDateTime('2026-11-17T01:58:24Z')).toBe('2026-11-17 09:58');
    expect(formatDateTime('not-a-date')).toBe('—');
  });
});

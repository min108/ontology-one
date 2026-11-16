import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { LegacyBlock } from './LegacyBlock';
import { Modal, ToastProvider, useToast } from '@legacy/components/ui';

vi.mock('@legacy/auth', () => ({ useAuth: () => ({ session: { permissions: {} } }) }));
afterEach(() => document.getElementById('legacy-portal-root')?.remove());

function Notify() {
  const notify = useToast();
  return <button onClick={() => notify('Toast fixture')}>notify</button>;
}

describe('legacy layout boundary', () => {
  it('marks a page by default', () => {
    const { container } = render(<LegacyBlock>fixture</LegacyBlock>);
    expect(container.firstElementChild).toHaveClass('legacy-root');
    expect(container.firstElementChild).toHaveAttribute('data-legacy-page');
  });
  it('lets floating widgets use a contents wrapper without a page marker', () => {
    const { container } = render(<LegacyBlock variant="contents">fixture</LegacyBlock>);
    expect(container.firstElementChild).toHaveClass('legacy-root');
    expect(container.firstElementChild).not.toHaveAttribute('data-legacy-page');
  });
  it('supports explicit marker and standard div attributes', () => {
    render(
      <LegacyBlock variant="contents" page id="fixture-block">
        fixture
      </LegacyBlock>,
    );
    expect(document.getElementById('fixture-block')).toHaveAttribute('data-legacy-page');
  });
  it('scopes a modal in a non-layout portal and preserves body locking', () => {
    const previous = document.body.style.overflow;
    const { unmount } = render(
      <Modal title="Modal fixture" onClose={() => {}}>
        fixture
      </Modal>,
    );
    const root = screen.getByText('Modal fixture').closest('.legacy-root');
    expect(root).toHaveAttribute('id', 'legacy-portal-root');
    expect(root).toHaveStyle({ display: 'contents', lineHeight: 'normal' });
    expect(document.body.style.overflow).toBe('hidden');
    unmount();
    expect(document.body.style.overflow).toBe(previous);
  });
  it('scopes toast output while retaining the provider outside the old page', () => {
    render(
      <ToastProvider>
        <Notify />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'notify' }));
    expect(screen.getByText('Toast fixture').closest('.legacy-root')).toHaveAttribute(
      'id',
      'legacy-portal-root',
    );
    fireEvent(window, new Event('ontology:scope-cleared'));
    expect(screen.queryByText('Toast fixture')).not.toBeInTheDocument();
  });
  it('shares one portal for modal and toast nodes', () => {
    render(
      <ToastProvider>
        <Modal title="Modal fixture" onClose={() => {}}>
          fixture
        </Modal>
        <Notify />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'notify' }));
    expect(document.querySelectorAll('#legacy-portal-root')).toHaveLength(1);
  });
});

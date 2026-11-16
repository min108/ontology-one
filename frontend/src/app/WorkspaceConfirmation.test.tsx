import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DesignProvider } from '../design/DesignProvider';
import { terms } from '../i18n/zh-CN';
import type { WorkspaceGuardSnapshot } from '../state/workspace-guard';
import { WorkspaceConfirmation } from './WorkspaceConfirmation';

function setup() {
  let confirm: ((snapshot: WorkspaceGuardSnapshot) => Promise<boolean>) | undefined;
  const unsubscribe = vi.fn();
  const register = (callback: NonNullable<typeof confirm>) => {
    confirm = callback;
    return unsubscribe;
  };
  const view = render(
    <DesignProvider>
      <WorkspaceConfirmation register={register} />
    </DesignProvider>,
  );
  return {
    ...view,
    unsubscribe,
    open: (snapshot: WorkspaceGuardSnapshot) => {
      let answer: Promise<boolean> | undefined;
      act(() => {
        answer = confirm?.(snapshot);
      });
      return answer!;
    },
  };
}

describe('workspace confirmation user decisions', () => {
  it('describes edits, uploads and stored keys without treating counts as documents', async () => {
    const view = setup();
    const answer = view.open({ dirtyCount: 7, uploadCount: 3, storedDraftCount: 5 });
    const dialog = await screen.findByRole('dialog', { name: terms.labels.shell_switch_title });
    expect(dialog).toHaveTextContent(terms.labels.shell_switch_dirty);
    expect(dialog).toHaveTextContent(terms.labels.shell_switch_upload);
    expect(dialog).toHaveTextContent(terms.labels.shell_switch_stored);
    expect(dialog.textContent).not.toMatch(/[357]/);
    fireEvent.click(screen.getByRole('button', { name: terms.actions.cancel }));
    await expect(answer).resolves.toBe(false);
  });
  it('accepts only the explicit confirm action and omits absent risks', async () => {
    const view = setup();
    const answer = view.open({ dirtyCount: 1, uploadCount: 0, storedDraftCount: 0 });
    await screen.findByRole('dialog');
    expect(screen.queryByText(terms.labels.shell_switch_upload)).not.toBeInTheDocument();
    expect(screen.queryByText(terms.labels.shell_switch_stored)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: terms.actions.shell_switch_confirm }));
    await expect(answer).resolves.toBe(true);
  });
  it('denies the pending switch when its owning workspace unmounts', async () => {
    const view = setup();
    const answer = view.open({ dirtyCount: 1, uploadCount: 0, storedDraftCount: 0 });
    await screen.findByRole('dialog');
    view.unmount();
    await expect(answer).resolves.toBe(false);
    expect(view.unsubscribe).toHaveBeenCalledOnce();
  });
});

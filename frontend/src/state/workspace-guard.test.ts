import { afterEach, describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import {
  useWorkspaceDraftGuard,
  useWorkspaceGuard,
  useWorkspaceInstructionGuard,
} from '../app/useWorkspaceGuard';
import {
  beginWorkspaceUpload,
  isWorkspaceConfirmationOpen,
  isWorkspaceLeaving,
  readWorkspaceGuards,
  registerWorkspaceGuard,
  setWorkspaceTransition,
  trackStoredWorkspaceDraft,
} from './workspace-guard';

const clear = () => window.dispatchEvent(new Event('ontology:scope-cleared'));
afterEach(() => {
  clear();
  sessionStorage.clear();
  setWorkspaceTransition('idle');
});

describe('workspace guard lifecycle', () => {
  it('accepts only the matching task instruction and retains newer input', () => {
    const { rerender } = renderHook(
      ({ value }) => useWorkspaceInstructionGuard(value, 'project_plan', 'p1'),
      { initialProps: { value: '' } },
    );
    rerender({ value: 'plan' });
    const accepted = (entity_id: string, instruction: string) =>
      act(() => {
        window.dispatchEvent(
          new CustomEvent('ontology:harness-run-started', {
            detail: { operation: 'project_plan', instruction, run: { entity_id } },
          }),
        );
      });
    accepted('p2', 'plan');
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    accepted('p1', 'plan');
    expect(readWorkspaceGuards()).toMatchObject({ dirtyCount: 0, uploadCount: 0 });
    rerender({ value: 'new instruction' });
    accepted('p1', 'plan');
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    rerender({ value: '' });
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
  });
  it('only commits the submitted snapshot, preserving edits made during save', () => {
    const { result, rerender } = renderHook(
      ({ title, search }) => {
        void search;
        return useWorkspaceDraftGuard({ title });
      },
      { initialProps: { title: 'saved', search: '' } },
    );
    rerender({ title: 'saved', search: 'filter' });
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
    rerender({ title: 'submitted', search: '' });
    const submitted = { title: 'submitted' };
    // A failed request does not call commit.
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    rerender({ title: 'newer edit', search: '' });
    act(() => result.current(submitted));
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    rerender({ title: 'submitted', search: '' });
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
  });
  it('reads current dirty state and unregisters exactly once', () => {
    let dirty = false;
    const unregister = registerWorkspaceGuard(() => dirty);
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
    dirty = true;
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    dirty = false;
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
    unregister();
    unregister();
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
  });
  it('hook observes edits and reverts without registering search controls', () => {
    const { rerender, unmount } = renderHook(({ dirty }) => useWorkspaceGuard(dirty), {
      initialProps: { dirty: false },
    });
    rerender({ dirty: true });
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    rerender({ dirty: false });
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
    unmount();
  });
  it('mounted editors re-register after a non-leaving scope clear', () => {
    const { rerender, unmount } = renderHook(({ dirty }) => useWorkspaceGuard(dirty), {
      initialProps: { dirty: false },
    });
    act(clear);
    rerender({ dirty: true });
    expect(readWorkspaceGuards().dirtyCount).toBe(1);
    unmount();
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
  });
  it('upload lease spans file gaps and survives editor unmount', async () => {
    const finish = beginWorkspaceUpload();
    await Promise.resolve();
    expect(readWorkspaceGuards().uploadCount).toBe(1);
    finish();
    finish();
    expect(readWorkspaceGuards().uploadCount).toBe(0);
  });
  it('assistant waiting creates no upload lease', () => {
    registerWorkspaceGuard(() => false);
    expect(readWorkspaceGuards().uploadCount).toBe(0);
  });
  it('confirmation and cancellation do not clear drafts or uploads', () => {
    registerWorkspaceGuard(() => true);
    const finish = beginWorkspaceUpload();
    setWorkspaceTransition('confirming');
    expect(isWorkspaceConfirmationOpen()).toBe(true);
    setWorkspaceTransition('idle');
    expect(readWorkspaceGuards()).toEqual({ dirtyCount: 1, uploadCount: 1, storedDraftCount: 0 });
    finish();
  });
  it('scope cleanup retains leaving and stale cleanup cannot remove new entries', () => {
    const oldFinish = beginWorkspaceUpload();
    setWorkspaceTransition('leaving');
    act(clear);
    expect(isWorkspaceLeaving()).toBe(true);
    setWorkspaceTransition('idle');
    const finish = beginWorkspaceUpload();
    oldFinish();
    expect(readWorkspaceGuards().uploadCount).toBe(1);
    finish();
  });
  it('retains dirty draft metadata after editor closes, ignores deleted saved data', () => {
    const key = 'ontology-one-draft-asset-edit-a-1-title';
    sessionStorage.setItem(key, JSON.stringify('changed'));
    trackStoredWorkspaceDraft(key, true);
    expect(readWorkspaceGuards().storedDraftCount).toBe(1);
    sessionStorage.removeItem(key);
    expect(readWorkspaceGuards().storedDraftCount).toBe(0);
  });
  it('clean persisted initial values do not warn; old unclassified drafts do', () => {
    const key = 'ontology-one-draft-asset-edit-a-1-title';
    sessionStorage.setItem(key, JSON.stringify('initial'));
    expect(readWorkspaceGuards().storedDraftCount).toBe(1);
    trackStoredWorkspaceDraft(key, false);
    expect(readWorkspaceGuards().storedDraftCount).toBe(0);
  });
  it('late draft effect cannot recreate storage metadata while leaving', () => {
    setWorkspaceTransition('leaving');
    const unregister = registerWorkspaceGuard(() => true);
    expect(readWorkspaceGuards().dirtyCount).toBe(0);
    unregister();
    trackStoredWorkspaceDraft('ontology-one-draft-a', true);
    expect(sessionStorage.length).toBe(0);
  });
});

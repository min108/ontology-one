/** Explicit business state only: never infer edits from input or toast events. */
export type WorkspaceGuardSnapshot = {
  dirtyCount: number;
  uploadCount: number;
  storedDraftCount: number;
};
type Transition = 'idle' | 'confirming' | 'leaving';
const guards = new Map<symbol, () => boolean>();
const uploads = new Set<symbol>();
const draftPrefix = 'ontology-one-draft-';
const metadataPrefix = 'ontology-one.guard-draft:';
let transition: Transition = 'idle';

export function registerWorkspaceGuard(readDirty: () => boolean): () => void {
  if (transition === 'leaving') return () => {};
  const id = Symbol();
  guards.set(id, readDirty);
  return () => {
    guards.delete(id);
  };
}

/** Keep this lease until the complete batch finishes, including inter-file gaps. */
export function beginWorkspaceUpload(): () => void {
  const id = Symbol();
  if (transition !== 'leaving') uploads.add(id);
  return () => {
    uploads.delete(id);
  };
}

export function trackStoredWorkspaceDraft(storageKey: string, dirty: boolean): void {
  if (transition === 'leaving') return;
  try {
    sessionStorage.setItem(metadataPrefix + storageKey, dirty ? 'dirty' : 'clean');
  } catch {
    /* Storage may be disabled. */
  }
}

export function readWorkspaceGuards(): WorkspaceGuardSnapshot {
  let storedDraftCount = 0;
  try {
    for (const key of Object.keys(sessionStorage)) {
      // Old drafts without metadata are browser temporaries, not proven edits.
      if (key.startsWith(draftPrefix) && sessionStorage.getItem(metadataPrefix + key) !== 'clean')
        storedDraftCount++;
    }
  } catch {
    /* Mounted editors still report their actual dirty state. */
  }
  return {
    dirtyCount: [...guards.values()].filter((probe) => probe()).length,
    uploadCount: uploads.size,
    storedDraftCount,
  };
}

export function setWorkspaceTransition(phase: Transition): void {
  transition = phase;
}
export function isWorkspaceConfirmationOpen(): boolean {
  return transition === 'confirming';
}
export function isWorkspaceLeaving(): boolean {
  return transition === 'leaving';
}

window.addEventListener('ontology:scope-cleared', () => {
  guards.clear();
  uploads.clear();
  // Keep leaving until the hard reload; stale effects must remain blocked.
});

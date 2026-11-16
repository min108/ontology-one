import { create } from 'zustand';
import {
  assertBusinessReady,
  setCsrfToken,
  setWorkspaceId,
  setBusinessBlocked,
  invalidateRequests,
} from '@legacy/api';
import { api, ApiError, invalidateClientRequests, invokeCapability } from '../api/client';
import type { AuthSession, SessionIdentity } from '../api/session.types';
import { queryClient } from '../api/query';
import { terms } from '../i18n/zh-CN';
import { useScopeStore } from './scope';
import { defaultViewMode, useUiStore } from './ui';
import { clearPreferences } from './preferences';
import {
  isWorkspaceLeaving,
  readWorkspaceGuards,
  setWorkspaceTransition,
  type WorkspaceGuardSnapshot,
} from './workspace-guard';

type Confirmation = (snapshot: WorkspaceGuardSnapshot) => Promise<boolean>;
let confirmation: Confirmation | undefined;
let transitionLocked = false;
let revision = 0;
let sessionController: AbortController | undefined;

function cancelSessionOperation(): number {
  ++revision;
  sessionController?.abort();
  sessionController = undefined;
  return revision;
}

/** Cancel only superseded identity work, before the client can handle its status. */
function beginSessionOperation(): { token: number; signal: AbortSignal } {
  const token = cancelSessionOperation();
  sessionController = new AbortController();
  return { token, signal: sessionController.signal };
}
export function setWorkspaceConfirmation(callback: Confirmation): () => void {
  confirmation = callback;
  return () => {
    if (confirmation === callback) confirmation = undefined;
  };
}
function current(token: number): boolean {
  if (token !== revision || isWorkspaceLeaving()) return false;
  // Legacy 409 handling also blocks business requests before its hard reload.
  // No late session response may undo that block, even outside this store.
  try {
    assertBusinessReady();
    return true;
  } catch {
    return false;
  }
}

/** Also retained as the legacy password-change cleanup API. */
export function clearLocalWorkspace(): void {
  clearPreferences();
  invalidateClientRequests();
  void queryClient.cancelQueries().catch(() => undefined);
  queryClient.clear();
  useScopeStore.getState().resetScope();
  useUiStore.getState().resetScopeUi();
  try {
    invalidateRequests();
    for (const storage of [sessionStorage, localStorage])
      for (const key of Object.keys(storage))
        if (key.startsWith('ontology-one-') || key.startsWith('ontology-one.')) storage.removeItem(key);
  } finally {
    window.dispatchEvent(new Event('ontology:scope-cleared'));
  }
}
export type SessionState = {
  session: AuthSession | null;
  loading: boolean;
  error: string;
  switching: boolean;
  revision: number;
  notice: string;
  permission_keys: string[];
  permissionError: string;
  setNotice: (message: string) => void;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchWorkspace: (tenant_id: string) => Promise<void>;
  acceptSession: (session: AuthSession) => void;
  reload: () => Promise<void>;
};
async function accept(next: AuthSession, token: number, signal: AbortSignal): Promise<void> {
  if (!current(token)) return;
  const previous = useSessionStore.getState().session;
  if (
    previous &&
    (previous.workspace.tenant_id !== next.workspace.tenant_id || previous.user.id !== next.user.id)
  ) {
    // A session refreshed after another tab switched cannot reuse mounted legacy state.
    setWorkspaceTransition('leaving');
    setBusinessBlocked(true);
    cancelSessionOperation();
    useSessionStore.setState({ switching: true, permission_keys: [] });
    try {
      clearLocalWorkspace();
    } finally {
      window.location.replace('/');
    }
    return;
  }
  setCsrfToken(next.csrf_token);
  setWorkspaceId(next.workspace.tenant_id);
  const newIdentity =
    previous?.workspace.tenant_id !== next.workspace.tenant_id || previous.user.id !== next.user.id;
  useScopeStore.getState().setScope(next.workspace.tenant_id);
  if (newIdentity) useUiStore.getState().resetScopeUi(defaultViewMode(next.user.role));
  useSessionStore.setState((state) => ({
    session: next,
    error: '',
    permission_keys: [],
    permissionError: '',
    revision: state.revision + 1,
  }));
  try {
    const identity = await invokeCapability<SessionIdentity>('session.whoami', {}, { signal });
    if (!current(token)) return;
    if (
      identity.user_id !== next.user.id ||
      identity.tenant_id !== next.workspace.tenant_id ||
      !Array.isArray(identity.permissions) ||
      !identity.permissions.every((key) => typeof key === 'string')
    )
      throw new Error('Session identity mismatch');
    useSessionStore.setState({ permission_keys: [...identity.permissions] });
    if (newIdentity && identity.permissions.length === 1 && identity.permissions[0] === 'source.submit')
      useUiStore.getState().setViewMode('simple');
  } catch {
    if (current(token))
      useSessionStore.setState({
        permission_keys: [],
        permissionError: terms.labels.session_permissions_failed,
      });
  }
}
function clearIdentity(): void {
  setCsrfToken('');
  setWorkspaceId('');
  useSessionStore.setState((state) => ({
    session: null,
    loading: false,
    error: '',
    permission_keys: [],
    permissionError: '',
    revision: state.revision + 1,
  }));
}
function goHome(): void {
  window.history.replaceState({}, '', '/');
  window.dispatchEvent(new PopStateEvent('popstate'));
}
export const useSessionStore = create<SessionState>((set) => ({
  session: null,
  loading: true,
  error: '',
  switching: false,
  revision: 0,
  notice: '',
  permission_keys: [],
  permissionError: '',
  setNotice: (notice) => {
    if (!isWorkspaceLeaving()) set({ notice });
  },
  acceptSession: (next) => {
    if (isWorkspaceLeaving()) return;
    const { token, signal } = beginSessionOperation();
    void accept(next, token, signal).finally(() => {
      if (current(token)) set({ loading: false });
    });
  },
  reload: async () => {
    if (isWorkspaceLeaving()) return;
    const { token, signal } = beginSessionOperation();
    set({ loading: true, error: '' });
    try {
      await accept(await api<AuthSession>('/auth/session', { signal }), token, signal);
    } catch (error) {
      if (current(token)) {
        try {
          clearLocalWorkspace();
        } finally {
          clearIdentity();
        }
        if (
          !(error instanceof DOMException && error.name === 'AbortError') &&
          !(error instanceof ApiError && error.status === 401)
        )
          set({ error: terms.labels.session_load_failed });
      }
    } finally {
      if (current(token)) set({ loading: false });
    }
  },
  login: async (username, password) => {
    if (isWorkspaceLeaving()) return;
    const { token, signal } = beginSessionOperation();
    set({ notice: '' });
    clearLocalWorkspace();
    const next = await api<AuthSession>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
      signal,
    });
    await accept(next, token, signal);
    if (current(token)) {
      set({ loading: false });
      goHome();
    }
  },
  logout: async () => {
    if (isWorkspaceLeaving()) return;
    const { token, signal } = beginSessionOperation();
    await api('/auth/logout', { method: 'POST', signal });
    if (!current(token)) return;
    clearLocalWorkspace();
    clearIdentity();
    set({ notice: '' });
    goHome();
  },
  switchWorkspace: async (tenant_id) => {
    if (
      transitionLocked ||
      isWorkspaceLeaving() ||
      tenant_id === useSessionStore.getState().session?.workspace.tenant_id
    )
      return;
    transitionLocked = true;
    try {
      const snapshot = readWorkspaceGuards();
      if (snapshot.dirtyCount || snapshot.uploadCount || snapshot.storedDraftCount) {
        setWorkspaceTransition('confirming');
        if (!confirmation) return;
        try {
          if (!(await confirmation(snapshot))) return;
        } catch {
          return;
        }
      }
      if (isWorkspaceLeaving()) return;
      setWorkspaceTransition('leaving');
      setBusinessBlocked(true);
      cancelSessionOperation();
      set({ switching: true, permission_keys: [] });
      try {
        clearLocalWorkspace();
        await api('/auth/workspace', { method: 'POST', body: JSON.stringify({ tenant_id }) });
      } catch {
        /* A hard reload resolves both uncertain POST results and cleanup failures. */
      } finally {
        window.location.replace('/');
      }
    } finally {
      if (!isWorkspaceLeaving()) {
        transitionLocked = false;
        setWorkspaceTransition('idle');
      }
    }
  },
}));

/** React StrictMode may mount twice: generation checks reject the first late load. */
export function initializeSession(): () => void {
  const expired = () => {
    if (isWorkspaceLeaving()) return;
    cancelSessionOperation();
    try {
      clearLocalWorkspace();
    } finally {
      clearIdentity();
    }
  };
  window.addEventListener('ontology:unauthorized', expired);
  void useSessionStore.getState().reload();
  return () => window.removeEventListener('ontology:unauthorized', expired);
}

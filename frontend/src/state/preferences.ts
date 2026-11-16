import { create } from 'zustand';
import { api } from '../api/client';
import { fromPreferenceViewMode, toPreferenceViewMode, useUiStore, type ViewMode } from './ui';

type PreferenceState = { saving: boolean; error: '' | 'load' | 'save'; loaded: boolean };
export const usePreferenceStore = create<PreferenceState>(() => ({
  saving: false,
  error: '',
  loaded: false,
}));
type Context = {
  userId: string;
  tenantId: string;
  controller: AbortController;
  choice: number;
  loaded: boolean;
  loading?: Promise<void>;
  pending?: ViewMode;
  flushing: boolean;
};
let context: Context | undefined;
const alive = (owner: Context): boolean => context === owner && !owner.controller.signal.aborted;
export function clearPreferences(): void {
  context?.controller.abort();
  context = undefined;
  usePreferenceStore.setState({ saving: false, error: '', loaded: false });
}
export async function loadPreferences(userId: string, tenantId: string): Promise<void> {
  if (context?.userId !== userId || context.tenantId !== tenantId) {
    clearPreferences();
    context = {
      userId,
      tenantId,
      controller: new AbortController(),
      choice: 0,
      loaded: false,
      flushing: false,
    };
  }
  const owner = context;
  if (owner.loading || owner.loaded) return owner.loading;
  const choice = owner.choice;
  owner.loading = (async () => {
    try {
      const result = await api<{ preferences: Record<string, unknown> }>('/v2/prefs', {
        signal: owner.controller.signal,
      });
      if (!alive(owner)) return;
      if (!result || typeof result.preferences !== 'object' || result.preferences === null)
        throw new Error('Invalid preferences');
      const mode = result.preferences.view_mode;
      if (mode !== undefined && mode !== 'simple' && mode !== 'pro')
        throw new Error('Invalid preference mode');
      if (choice === 0 && owner.choice === 0 && (mode === 'simple' || mode === 'pro'))
        useUiStore.getState().setViewMode(fromPreferenceViewMode(mode));
      owner.loaded = true;
      usePreferenceStore.setState({
        loaded: true,
        ...(usePreferenceStore.getState().error === 'load' ? { error: '' as const } : {}),
      });
    } catch {
      if (alive(owner) && usePreferenceStore.getState().error !== 'save')
        usePreferenceStore.setState({ error: 'load' });
    } finally {
      owner.loading = undefined;
    }
  })();
  return owner.loading;
}
async function flush(owner: Context): Promise<void> {
  if (!alive(owner) || owner.flushing) return;
  owner.flushing = true;
  usePreferenceStore.setState({ saving: true, error: '' });
  try {
    while (alive(owner) && owner.pending !== undefined) {
      const selected = owner.pending;
      owner.pending = undefined;
      try {
        await api('/v2/prefs/view_mode', {
          method: 'PUT',
          body: JSON.stringify({ value: toPreferenceViewMode(selected) }),
          signal: owner.controller.signal,
        });
        if (!alive(owner)) return;
      } catch {
        if (!alive(owner)) return;
        if (owner.pending === undefined) {
          owner.pending = selected;
          usePreferenceStore.setState({ error: 'save' });
          break;
        }
      }
    }
  } finally {
    owner.flushing = false;
    if (alive(owner)) usePreferenceStore.setState({ saving: false });
  }
}
export function savePreferredViewMode(mode: ViewMode): void {
  if (!context || !alive(context)) return;
  context.choice++;
  context.pending = mode;
  useUiStore.getState().setViewMode(mode);
  void flush(context);
}
export function retryPreferenceSave(): void {
  if (context) void flush(context);
}

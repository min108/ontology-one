import { create } from 'zustand';

export type ViewMode = 'simple' | 'professional';
export type PreferenceViewMode = 'simple' | 'pro';
export function fromPreferenceViewMode(mode: PreferenceViewMode): ViewMode {
  return mode === 'pro' ? 'professional' : 'simple';
}
export function toPreferenceViewMode(mode: ViewMode): PreferenceViewMode {
  return mode === 'professional' ? 'pro' : 'simple';
}
export function defaultViewMode(role: string, permissions: readonly string[] = []): ViewMode {
  if (
    role === 'viewer' ||
    role === 'analyst' ||
    (permissions.length === 1 && permissions[0] === 'source.submit')
  )
    return 'simple';
  return ['admin', 'modeler', 'reviewer'].includes(role) ? 'professional' : 'simple';
}
type UiState = {
  viewMode: ViewMode;
  collapsed: boolean;
  setViewMode: (viewMode: ViewMode) => void;
  setCollapsed: (collapsed: boolean) => void;
  resetScopeUi: (viewMode?: ViewMode) => void;
};
export const useUiStore = create<UiState>((set) => ({
  viewMode: 'simple',
  collapsed: false,
  setViewMode: (viewMode) => set({ viewMode }),
  setCollapsed: (collapsed) => set({ collapsed }),
  resetScopeUi: (viewMode = 'simple') => set({ viewMode, collapsed: false }),
}));

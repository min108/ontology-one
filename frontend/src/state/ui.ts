import { create } from 'zustand';

export type ViewMode = 'simple' | 'professional';
export type PreferenceViewMode = 'simple' | 'pro';
export function fromPreferenceViewMode(mode: PreferenceViewMode): ViewMode {
  return mode === 'pro' ? 'professional' : 'simple';
}
export function toPreferenceViewMode(mode: ViewMode): PreferenceViewMode {
  return mode === 'professional' ? 'pro' : 'simple';
}
export function defaultViewMode(_role: string, _permissions: readonly string[] = []): ViewMode {
  return 'simple';
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

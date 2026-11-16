import { create } from 'zustand';

type ScopeState = {
  tenantId: string;
  scopeKey: string;
  setScope: (tenantId: string, scopeKey?: string) => void;
  resetScope: () => void;
};
export const useScopeStore = create<ScopeState>((set) => ({
  tenantId: '',
  scopeKey: 'local',
  setScope: (tenantId, scopeKey = 'local') => set({ tenantId, scopeKey }),
  resetScope: () => set({ tenantId: '', scopeKey: 'local' }),
}));

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
// Each render owns a cache; no session, stale data or retry timers survive another test.
export function testQuery() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return function TestQuery({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
export function testSession() {
  useScopeStore.getState().setScope('tenant-test');
  useSessionStore.setState({
    session: {
      authenticated: true,
      user: {
        id: 'user-test',
        username: 'builder',
        display_name: '建设人员',
        home_tenant_id: 'tenant-test',
        role: 'modeler',
        is_superadmin: false,
        must_change_password: false,
      },
      workspace: { tenant_id: 'tenant-test', name: '测试机构', category: 'local', readonly: false },
      workspaces: [],
      roles: [],
      permissions: { can_edit: true, can_review: true, manage_users: false, manage_grants: false },
      csrf_token: 'test',
      must_change_password: false,
    },
  });
}

import { QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from '@legacy/auth';
import { ToastProvider } from '@legacy/components/ui';
import { NavigationProvider } from '@legacy/navigation';
import { queryClient } from '../api/query';
import { AuthBoundary } from '../features/auth/AuthBoundary';
import { PreferencesBridge } from '../features/account/PreferencesBridge';
import { WorkspaceShell } from './WorkspaceShell';

function WorkspaceApp() {
  const { session, loading, switching, revision } = useAuth();
  if (loading || !session || switching) return <AuthBoundary>{null}</AuthBoundary>;
  return (
    <NavigationProvider key={`${session.user.id}:${session.workspace.tenant_id}:${revision}`}>
      <WorkspaceShell />
    </NavigationProvider>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ToastProvider>
          <PreferencesBridge />
          <WorkspaceApp />
        </ToastProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

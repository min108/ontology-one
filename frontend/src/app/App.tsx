import { QueryClientProvider } from '@tanstack/react-query';
import { AuthGate, AuthProvider, useAuth } from '@legacy/auth';
import { ToastProvider } from '@legacy/components/ui';
import { NavigationProvider } from '@legacy/navigation';
import { queryClient } from '../api/query';
import { LegacyBlock } from '../ui/LegacyBlock';
import { WorkspaceShell } from './WorkspaceShell';

function WorkspaceApp() {
  const { session, loading, switching, revision } = useAuth();
  if (loading || !session || session.must_change_password || session.user.must_change_password || switching)
    return (
      <LegacyBlock page={false}>
        <AuthGate>{null}</AuthGate>
      </LegacyBlock>
    );
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
          <WorkspaceApp />
        </ToastProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}

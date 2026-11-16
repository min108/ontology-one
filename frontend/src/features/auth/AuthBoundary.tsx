import type { ReactNode } from 'react';
import { useSessionStore } from '../../state/session';
import { DesignProvider } from '../../design/DesignProvider';
import { StateBlock } from '../../ui/StateBlock';
import { LoginPage } from './LoginPage';
import { copy, action } from '../identity/common';
export function AuthBoundary({ children }: { children: ReactNode }) {
  const { session, loading, switching, reload } = useSessionStore();
  if (loading || switching)
    return (
      <DesignProvider>
        <StateBlock
          state="loading"
          phase={switching ? copy.identity_switching_help : copy.identity_session_loading}
          action={{ label: action.identity_reconnect, onClick: () => void reload(), disabled: switching }}
        />
      </DesignProvider>
    );
  if (!session)
    return (
      <DesignProvider>
        <LoginPage />
      </DesignProvider>
    );
  return <>{children}</>;
}

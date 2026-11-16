import { useEffect } from 'react';
import { useSessionStore } from '../../state/session';
import { loadPreferences } from '../../state/preferences';
export function PreferencesBridge() {
  const { session, loading, revision } = useSessionStore();
  const userId = session?.user.id,
    tenantId = session?.workspace.tenant_id;
  useEffect(() => {
    if (userId && tenantId && !loading) void loadPreferences(userId, tenantId);
  }, [userId, tenantId, loading, revision]);
  return null;
}

import { useLocation } from 'react-router-dom';
import { useAuth } from '@legacy/auth';
import EntityVisibility from '@legacy/components/EntityVisibility';

export function LegacyOwnership() {
  const { session } = useAuth();
  const { pathname } = useLocation();
  const match =
    pathname.match(/^\/(?:assets|authority|materials|datasets|projects|tasks)\/([^/]+)$/) ??
    pathname.match(/^\/knowledge\/collections\/([^/]+)$/);
  if (!match || !session) return null;
  return (
    <EntityVisibility
      entityId={decodeURIComponent(match[1])}
      tenantId={session.workspace.tenant_id}
      canEdit={session.permissions.can_edit}
    />
  );
}

import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
import { isWorkspaceLeaving } from '../../state/workspace-guard';
import { terms } from '../../i18n/zh-CN';
import { recordRecent, recentHref } from './recent';
export function RecentRecorder() {
  const session = useSessionStore((state) => state.session),
    scopeKey = useScopeStore((state) => state.scopeKey);
  const location = useLocation();
  useEffect(() => {
    if (!session || isWorkspaceLeaving() || window.location.pathname !== location.pathname) return;
    const href = recentHref(location.pathname + location.search + location.hash);
    if (!href) return;
    const id = location.pathname.split('/').at(-1) ?? '';
    let label = id;
    try {
      label = decodeURIComponent(id);
    } catch {
      /* Keep a safe encoded object identifier. */
    }
    recordRecent(
      { userId: session.user.id, tenantId: session.workspace.tenant_id, scopeKey },
      { href, title: terms.labels.home_recent_object + ' ' + label },
    );
  }, [session, scopeKey, location.pathname, location.search, location.hash]);
  return null;
}

import { useEffect, useRef } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
import { isWorkspaceConfirmationOpen, isWorkspaceLeaving } from '../../state/workspace-guard';
import { acknowledgeTransfer, readTransfer, type IntakeTarget } from './intake';
/** Read repeatedly, acknowledge only after queue insertion; StrictMode cannot consume twice. */
export function useIncomingFiles(
  target: IntakeTarget,
  receive: (files: File[]) => boolean | void,
  enabled = true,
  route = '/' + target,
): void {
  const session = useSessionStore((state) => state.session),
    scopeKey = useScopeStore((state) => state.scopeKey);
  const { pathname, key: locationKey } = useLocation();
  const [params, setParams] = useSearchParams();
  const id = params.get('intake') ?? '';
  const accepted = useRef(new Set<string>());
  useEffect(() => {
    if (
      !enabled ||
      isWorkspaceConfirmationOpen() ||
      isWorkspaceLeaving() ||
      !session ||
      !session.permissions.can_edit ||
      session.workspace.readonly ||
      !id ||
      pathname !== route ||
      window.location.pathname !== route ||
      accepted.current.has(id)
    )
      return;
    const scope = { userId: session.user.id, tenantId: session.workspace.tenant_id, scopeKey };
    const transfer = readTransfer(id, scope, target);
    if (!transfer) return;
    if (receive(transfer.files) === false) return;
    accepted.current.add(id);
    acknowledgeTransfer(id, scope);
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete('intake');
        next.set('upload', '1');
        return next;
      },
      { replace: true },
    );
  }, [id, session, scopeKey, target, pathname, locationKey, receive, enabled, setParams, route]);
}

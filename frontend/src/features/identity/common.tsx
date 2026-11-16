import { useEffect, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Modal } from 'antd';
import { useNavigate } from 'react-router-dom';
import { api, ApiError } from '../../api/client';
import { workspaceQueryKey } from '../../api/query';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
import { useWorkspaceDraftGuard } from '../../app/useWorkspaceGuard';
import { StateBlock } from '../../ui/StateBlock';
import { terms } from '../../i18n/zh-CN';
import styles from './Identity.module.css';
export const copy = terms.labels;
export const action = terms.actions;
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : copy.identity_error_preserved;
}
export function requestId(error: unknown): string | undefined {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== 'object') return;
  const body = error.detail as { error?: { request_id?: unknown }; request_id?: unknown };
  const id = body.error?.request_id ?? body.request_id;
  return typeof id === 'string' ? id : undefined;
}
export const time = (value: string): string =>
  value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '—';
export function Timestamp({ value }: { value: string }) {
  return (
    <time data-visual-dynamic="timestamp" dateTime={value}>
      {time(value)}
    </time>
  );
}
export function useIdentityQuery<T>(resource: string, path: string, enabled = true) {
  const session = useSessionStore((state) => state.session);
  const scope = useScopeStore((state) => state.scopeKey);
  return useQuery({
    queryKey: workspaceQueryKey(
      session?.workspace.tenant_id ?? 'unavailable',
      scope,
      resource,
      session?.user.id,
      path,
    ),
    queryFn: ({ signal }) => {
      return api<T>(path, { signal });
    },
    enabled: !!session && enabled,
  });
}
export function Forbidden({ headquarters = false }: { headquarters?: boolean }) {
  const navigate = useNavigate();
  return (
    <StateBlock
      state="forbidden"
      description={headquarters ? copy.identity_hq_permission : copy.identity_no_permission}
      action={{ label: action.identity_back_home, onClick: () => navigate('/') }}
    />
  );
}
export function QueryState({
  query,
  children,
}: {
  query: { isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
  children: ReactNode;
}) {
  return query.isPending ? (
    <StateBlock
      state="loading"
      action={{ label: action.identity_refresh, onClick: () => void query.refetch() }}
    />
  ) : query.isError ? (
    <StateBlock
      state="error"
      description={errorMessage(query.error)}
      requestId={requestId(query.error)}
      action={{ label: action.identity_retry, onClick: () => void query.refetch() }}
    />
  ) : (
    <>{children}</>
  );
}
export function useNarrowEditor(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 1023px)').matches);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 1023px)');
    const change = () => setNarrow(media.matches);
    media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  }, []);
  return narrow;
}
export function EditDialog({
  title,
  impact,
  summary,
  value,
  children,
  valid,
  onSave,
  onClose,
  narrow = false,
}: {
  title: string;
  impact: string;
  summary: string;
  value: unknown;
  children: ReactNode;
  valid: boolean;
  onSave: () => Promise<void>;
  onClose: () => void;
  narrow?: boolean;
}) {
  const [review, setReview] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>();
  useWorkspaceDraftGuard(value);
  async function save() {
    setBusy(true);
    setError(undefined);
    try {
      await onSave();
      onClose();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      open
      title={title}
      onCancel={() => !busy && onClose()}
      closable={!busy}
      mask={{ closable: false }}
      footer={
        <div className={styles.actions}>
          <Button autoInsertSpace={false} disabled={busy} onClick={onClose}>
            {action.identity_cancel}
          </Button>
          {review && (
            <Button autoInsertSpace={false} disabled={busy} onClick={() => setReview(false)}>
              {action.identity_back_edit}
            </Button>
          )}
          <Button
            autoInsertSpace={false}
            aria-label={review ? action.identity_confirm : action.identity_review}
            type="primary"
            loading={busy}
            disabled={!valid || narrow}
            onClick={() => (review ? void save() : setReview(true))}
          >
            {review ? action.identity_confirm : action.identity_review}
          </Button>
        </div>
      }
    >
      <div className={styles.stack}>
        {narrow && <Alert title={copy.identity_narrow} type="info" />}
        {review ? (
          <>
            <h3>{summary}</h3>
            <p>{impact}</p>
          </>
        ) : (
          <fieldset aria-label={title} className={styles.editor} disabled={narrow || busy}>
            {children}
          </fieldset>
        )}
        {error !== undefined && (
          <Alert
            type="error"
            showIcon
            title={errorMessage(error)}
            description={
              requestId(error) && (
                <details>
                  <summary>{copy.identity_request_id}</summary>
                  {requestId(error)}
                </details>
              )
            }
          />
        )}
      </div>
    </Modal>
  );
}

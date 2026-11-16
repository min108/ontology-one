import { useState, type FormEvent } from 'react';
import { Alert, Button, Card, Input, Modal, Tag } from 'antd';
import { api } from '../../api/client';
import type { AuthSession } from '../../api/session.types';
import { clearLocalWorkspace, useSessionStore } from '../../state/session';
import { useWorkspaceGuard } from '../../app/useWorkspaceGuard';
import { roleLabel } from '../../app/identity';
import { PageHeader } from '../../ui/PageHeader';
import { StateBlock } from '../../ui/StateBlock';
import {
  copy,
  action,
  errorMessage,
  QueryState,
  Timestamp,
  useIdentityQuery,
  useNarrowEditor,
} from '../identity/common';
import { validPassword } from '../identity/validation';
import styles from '../identity/Identity.module.css';
type LoginSession = {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
  user_agent: string;
  ip_hint: string;
  tenant_id: string;
};
export default function AccountSecurity() {
  const auth = useSessionStore(),
    session = auth.session!;
  const sessions = useIdentityQuery<{ items: LoginSession[] }>('identity-sessions', '/auth/sessions');
  const narrow = useNarrowEditor();
  const [current, setCurrent] = useState(''),
    [password, setPassword] = useState(''),
    [confirm, setConfirm] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [success, setSuccess] = useState(false),
    [target, setTarget] = useState<LoginSession | null>(null),
    [revoking, setRevoking] = useState(false),
    [revokeError, setRevokeError] = useState('');
  useWorkspaceGuard(Boolean(current || password || confirm));
  const valid = Boolean(current) && validPassword(password) && password === confirm;
  async function change(event: FormEvent) {
    event.preventDefault();
    if (!valid || busy || narrow) return;
    setBusy(true);
    setError('');
    setSuccess(false);
    try {
      const next = await api<AuthSession>('/auth/password', {
        method: 'POST',
        body: JSON.stringify({ current_password: current, new_password: password }),
      });
      clearLocalWorkspace();
      auth.acceptSession(next);
      auth.setNotice(copy.identity_password_changed);
      setCurrent('');
      setPassword('');
      setConfirm('');
      setSuccess(true);
      void sessions.refetch();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  async function revoke() {
    if (!target) return;
    setRevoking(true);
    setRevokeError('');
    try {
      await api('/auth/sessions/' + encodeURIComponent(target.id), { method: 'DELETE' });
      const own = target.current;
      setTarget(null);
      if (own) await auth.reload();
      else void sessions.refetch();
    } catch (cause) {
      setRevokeError(errorMessage(cause));
    } finally {
      setRevoking(false);
    }
  }
  const fields = [
    { label: copy.identity_current_password, key: 'current', value: current, set: setCurrent },
    { label: copy.identity_new_password, key: 'new', value: password, set: setPassword },
    { label: copy.identity_confirm_password, key: 'confirm', value: confirm, set: setConfirm },
  ];
  return (
    <section aria-label={copy.identity_security} aria-busy={sessions.isPending} className={styles.page}>
      <PageHeader title={copy.identity_security} />
      {session.password_change_recommended && !success && (
        <Alert type="info" showIcon title={copy.identity_password_recommended} />
      )}
      <div className={styles.grid}>
        <div className={styles.stack}>
          <Card title={copy.identity_current_account}>
            <dl className={styles.facts}>
              {[
                [copy.identity_name, session.user.display_name],
                [copy.identity_username, session.user.username],
                [copy.identity_current_organization, session.workspace.name],
                [copy.identity_role, roleLabel(session.user.role)],
                [
                  copy.identity_permissions,
                  session.permissions.can_edit
                    ? copy.identity_can_edit
                    : session.permissions.can_review
                      ? copy.identity_can_review
                      : copy.identity_readonly,
                ],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            {session.user.is_superadmin && <Tag>{copy.identity_superadmin}</Tag>}
          </Card>
          <Card title={copy.identity_password_title}>
            <form className={styles.form} onSubmit={change}>
              <p className={styles.help}>{copy.identity_password_impact}</p>
              {narrow && <Alert title={copy.identity_narrow} type="info" />}
              {fields.map((field) => (
                <label className={styles.field} htmlFor={'identity-password-' + field.key} key={field.key}>
                  {field.label}
                  <Input.Password
                    id={'identity-password-' + field.key}
                    autoComplete={field.key === 'current' ? 'current-password' : 'new-password'}
                    required
                    maxLength={128}
                    value={field.value}
                    disabled={busy || narrow}
                    onChange={(event) => field.set(event.target.value)}
                  />
                </label>
              ))}
              <p className={styles.help}>{copy.identity_password_rule}</p>
              {confirm && password !== confirm && (
                <Alert type="error" title={copy.identity_password_mismatch} />
              )}{' '}
              {error && <Alert type="error" showIcon title={error} />}{' '}
              {success && <Alert type="success" showIcon title={copy.identity_password_changed} />}
              <Button
                autoInsertSpace={false}
                htmlType="submit"
                aria-label={action.identity_password_change}
                type="primary"
                loading={busy}
                disabled={!valid || narrow}
              >
                {action.identity_password_change}
              </Button>
            </form>
          </Card>
        </div>
        <Card
          title={copy.identity_sessions}
          extra={
            <Button autoInsertSpace={false} onClick={() => void sessions.refetch()}>
              {action.identity_refresh}
            </Button>
          }
        >
          <p className={styles.help}>{copy.identity_sessions_help}</p>
          <QueryState query={sessions}>
            {sessions.data?.items.length ? (
              sessions.data.items.map((row) => (
                <section className={styles.session} key={row.id}>
                  <strong>
                    {row.current ? copy.identity_current_device : copy.identity_other_device}{' '}
                    {row.current && <Tag>{copy.identity_current_session}</Tag>}
                  </strong>
                  <p>{row.user_agent || copy.identity_not_recorded}</p>
                  <dl className={styles.facts}>
                    <div>
                      <dt>{copy.identity_last_seen}</dt>
                      <dd>
                        <Timestamp value={row.last_seen_at} />
                      </dd>
                    </div>
                    <div>
                      <dt>{copy.identity_created_at}</dt>
                      <dd>
                        <Timestamp value={row.created_at} />
                      </dd>
                    </div>
                    <div>
                      <dt>{copy.identity_source}</dt>
                      <dd>{row.ip_hint || '—'}</dd>
                    </div>
                    <div>
                      <dt>{copy.identity_expires_at}</dt>
                      <dd>
                        <Timestamp value={row.expires_at} />
                      </dd>
                    </div>
                  </dl>
                  <Button
                    autoInsertSpace={false}
                    disabled={narrow}
                    onClick={() => {
                      setTarget(row);
                      setRevokeError('');
                    }}
                  >
                    {action.identity_revoke_session}
                  </Button>
                </section>
              ))
            ) : (
              <StateBlock
                state="empty"
                description={copy.identity_no_sessions}
                action={{ label: action.identity_refresh, onClick: () => void sessions.refetch() }}
              />
            )}
          </QueryState>
        </Card>
      </div>
      {target && (
        <Modal
          open
          title={copy.identity_session_revoke_title}
          onCancel={() => !revoking && setTarget(null)}
          closable={!revoking}
          footer={
            <div className={styles.actions}>
              <Button autoInsertSpace={false} disabled={revoking} onClick={() => setTarget(null)}>
                {action.identity_cancel}
              </Button>
              <Button
                autoInsertSpace={false}
                danger
                aria-label={action.identity_confirm_revoke}
                type="primary"
                loading={revoking}
                disabled={narrow}
                onClick={() => void revoke()}
              >
                {action.identity_confirm_revoke}
              </Button>
            </div>
          }
        >
          <p>{target.current ? copy.identity_session_revoke_current : copy.identity_session_revoke_other}</p>
          {revokeError && <Alert type="error" title={revokeError} />}
        </Modal>
      )}
    </section>
  );
}

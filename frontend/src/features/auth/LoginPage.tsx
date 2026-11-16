import { useState, type FormEvent } from 'react';
import { Alert, Button, Input } from 'antd';
import { useSessionStore } from '../../state/session';
import { brand } from '../../i18n/brand';
import { copy, action } from '../identity/common';
import styles from '../identity/Identity.module.css';
import { APP_VERSION } from '@legacy/version';
export function LoginPage() {
  const { login, reload, error: connectionError } = useSessionStore();
  const [username, setUsername] = useState(''),
    [password, setPassword] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !username.trim() || !password) return;
    setBusy(true);
    setError('');
    try {
      await login(username.trim(), password);
    } catch (cause) {
      const status = typeof cause === 'object' && cause && 'status' in cause ? cause.status : undefined;
      setError(
        status === 401
          ? copy.identity_login_failed
          : status === 429
            ? copy.identity_login_limited
            : copy.identity_login_unavailable,
      );
    } finally {
      setPassword('');
      setBusy(false);
    }
  }
  return (
    <section className={styles.login} aria-label={copy.identity_login} aria-busy={busy}>
      <div className={styles.brand}>
        <img src="/citic-mark.svg" alt="" />
        <h2>
          {brand.product_name} · {brand.product_name_en}
        </h2>
        <p>{brand.product_descriptor}</p>
        <p>{brand.tagline}</p>
      </div>
      <form className={[styles['login-card'], styles.form].join(' ')} onSubmit={submit}>
        <h1>{copy.identity_login}</h1>
        <p className={styles.help}>{copy.identity_login_help}</p>
        <label className={styles.field} htmlFor="identity-login-username">
          {copy.identity_login_account}
          <Input
            id="identity-login-username"
            autoFocus
            autoComplete="username"
            required
            maxLength={80}
            value={username}
            disabled={busy}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label className={styles.field} htmlFor="identity-login-password">
          {copy.identity_password}
          <Input.Password
            id="identity-login-password"
            autoComplete="current-password"
            required
            maxLength={128}
            value={password}
            disabled={busy}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {(error || connectionError) && (
          <Alert type="error" showIcon title={error || copy.identity_login_unavailable} />
        )}
        <Button
          autoInsertSpace={false}
          type="primary"
          aria-label={action.identity_login}
          htmlType="submit"
          loading={busy}
          disabled={!username.trim() || !password}
        >
          {action.identity_login}
        </Button>
        {connectionError && (
          <Button autoInsertSpace={false} onClick={() => void reload()}>
            {action.identity_reconnect}
          </Button>
        )}
        <p className={styles.help}>{copy.identity_login_contact}</p>
        <span className={styles.version}>
          {brand.product_name} {APP_VERSION}
        </span>
      </form>
    </section>
  );
}

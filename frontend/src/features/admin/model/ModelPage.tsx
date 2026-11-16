import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input } from 'antd';
import { Link } from 'react-router-dom';
import { api, invokeCapability } from '../../../api/client';
import { useWorkspaceGuard } from '../../../app/useWorkspaceGuard';
import { PageHeader } from '../../../ui/PageHeader';
import { StateBlock } from '../../../ui/StateBlock';
import {
  copy,
  modelLabel,
  ResourceState,
  useManagementSession,
  useResource,
  type ModelStatus,
} from '../shared';
import styles from '../Admin.module.css';

export default function ModelPage() {
  const status = useResource<ModelStatus>('/v2/system/model');
  const { session } = useManagementSession();
  return (
    <div className={styles.page} data-admin-ready={Boolean(status.data)}>
      <PageHeader title={copy.admin_model_title} />
      {status.data ? (
        <ModelForm key={`${session?.user.id}:${session?.workspace.tenant_id}`} initial={status.data} />
      ) : (
        <ResourceState error={status.error} reload={status.reload} />
      )}
    </div>
  );
}
function ModelForm({ initial }: { initial: ModelStatus }) {
  const { session, canManage } = useManagementSession();
  const canVerify = canManage && session?.workspace.tenant_id === session?.user.home_tenant_id;
  const [status, setStatus] = useState(initial);
  const [base, setBase] = useState(initial.base_url || 'https://api.deepseek.com/anthropic');
  const [model, setModel] = useState(initial.model || 'deepseek-flash');
  const [key, setKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [baseline, setBaseline] = useState({ base, model });
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('');
  const [lastAction, setLastAction] = useState<'save' | 'verify'>('save');
  const lifecycle = useRef<AbortController | null>(null);
  useWorkspaceGuard(Boolean(key || clearKey || base !== baseline.base || model !== baseline.model));
  useEffect(() => {
    const controller = new AbortController();
    lifecycle.current = controller;
    const clear = () => {
      setKey('');
      controller.abort();
    };
    window.addEventListener('pagehide', clear);
    window.addEventListener('ontology:scope-cleared', clear);
    return () => {
      controller.abort();
      window.removeEventListener('pagehide', clear);
      window.removeEventListener('ontology:scope-cleared', clear);
    };
  }, []);
  async function run(action: 'save' | 'verify') {
    const signal = lifecycle.current?.signal;
    if (!canManage || busy || signal?.aborted || (action === 'verify' && !canVerify)) return;
    setLastAction(action);
    setBusy(true);
    setError('');
    setMessage('');
    try {
      if (action === 'save') {
        const result = await invokeCapability<ModelStatus>(
          'model.configure',
          {
            base_url: base.trim(),
            model: model.trim(),
            ...(clearKey ? { clear_key: true } : key ? { api_key: key } : {}),
          },
          { signal },
        );
        if (signal?.aborted) return;
        setStatus(result);
        setBaseline({ base, model });
        setKey('');
        setClearKey(false);
        setMessage(copy.admin_saved);
        window.dispatchEvent(new Event('ontology:harness-configured'));
      } else {
        const result = await api<{ status: ModelStatus }>('/v2/system/model:test', {
          method: 'POST',
          signal,
        });
        if (signal?.aborted) return;
        setStatus(result.status);
        setMessage(copy.admin_verified);
        window.dispatchEvent(new Event('ontology:harness-configured'));
      }
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.admin_failure);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }
  const [diagnosticError, setDiagnosticError] = useState('');
  const [diagnosticRevision, setDiagnosticRevision] = useState(0);
  const [diagnostics, setDiagnostics] = useState<{
    message: string;
    checks: { id: string; label: string; ready: boolean }[];
  }>();
  useEffect(() => {
    const controller = new AbortController();
    setDiagnosticError('');
    setDiagnostics(undefined);
    void invokeCapability<typeof diagnostics>('agent.readiness', {}, { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setDiagnostics(value);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setDiagnosticError(failure instanceof Error ? failure.message : copy.admin_failure);
      });
    return () => controller.abort();
  }, [diagnosticRevision]);
  return (
    <>
      <section className={styles.section}>
        <h2>{modelLabel(status)}</h2>
        <p>
          {status.model} · {copy.admin_runtime_version} {status.version}
        </p>
        {!status.available && status.reason && <p role="status">{status.reason}</p>}
        {status.configuration_notice && <p>{status.configuration_notice}</p>}
        <p>{copy.admin_model_intro}</p>
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            void run('save');
          }}
        >
          <details>
            <summary>{copy.admin_connection}</summary>
            <label className={styles.field}>
              {copy.admin_address}
              <Input
                aria-label={copy.admin_address}
                type="url"
                required
                disabled={!canManage || busy}
                value={base}
                onChange={(event) => setBase(event.target.value)}
              />
            </label>
            <label className={styles.field}>
              {copy.admin_model_name}
              <Input
                aria-label={copy.admin_model_name}
                required
                maxLength={200}
                disabled={!canManage || busy}
                value={model}
                onChange={(event) => setModel(event.target.value)}
              />
            </label>
          </details>
          <label className={styles.field}>
            {copy.admin_key}
            <Input.Password
              aria-label={copy.admin_key}
              autoComplete="new-password"
              visibilityToggle={false}
              maxLength={4096}
              disabled={!canManage || busy || clearKey}
              value={key}
              onChange={(event) => setKey(event.target.value)}
            />
          </label>
          <p className={styles.muted}>{copy.admin_key_hint}</p>
          {canManage && status.configured && (
            <Checkbox
              checked={clearKey}
              disabled={busy}
              onChange={(event) => {
                setClearKey(event.target.checked);
                setKey('');
              }}
            >
              {copy.admin_clear_key}
            </Checkbox>
          )}
          {canManage ? (
            <div className={styles.actions}>
              <Button type="primary" htmlType="submit" disabled={busy || !base.trim() || !model.trim()}>
                {busy ? copy.admin_busy : copy.admin_save}
              </Button>
              <Button disabled={busy || !canVerify || !status.configured} onClick={() => void run('verify')}>
                {copy.admin_verify}
              </Button>
            </div>
          ) : (
            <p>{copy.admin_model_readonly}</p>
          )}
          {!canVerify && canManage && <p>{copy.admin_cross_tenant}</p>}
        </form>
        {message && <p role="status">{message}</p>}
        {error && (
          <StateBlock
            state="error"
            description={error}
            action={{ label: copy.admin_retry_action, onClick: () => void run(lastAction), disabled: busy }}
          />
        )}
        {!status.configured && (
          <StateBlock
            state="unconfigured"
            action={{
              label: copy.admin_back,
              onClick: () => {
                window.location.assign('/');
              },
            }}
          />
        )}
      </section>
      <section className={styles.section}>
        <h2>{copy.admin_diagnostics}</h2>
        {diagnostics ? (
          <>
            <p>{diagnostics.message}</p>
            <ul>
              {diagnostics.checks.map((check) => (
                <li key={check.id}>{check.label}</li>
              ))}
            </ul>
          </>
        ) : (
          <ResourceState error={diagnosticError} reload={() => setDiagnosticRevision((value) => value + 1)} />
        )}
        <Link to="/admin/system">{copy.admin_system_title}</Link>
      </section>
    </>
  );
}

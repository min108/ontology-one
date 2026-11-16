import { useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { apiBlob, invokeCapability } from '../../../api/client';
import { PageHeader } from '../../../ui/PageHeader';
import { StateBlock } from '../../../ui/StateBlock';
import { copy, ResourceState, useManagementSession, useResource } from '../shared';
import styles from '../Admin.module.css';

interface Backups {
  items: { created_at: string; control_version: number; verified: boolean }[];
}
export default function BackupPage() {
  const { session, canManage } = useManagementSession();
  const canPackage = canManage && session?.user.is_superadmin && session.user.home_tenant_id === 'hq';
  const backups = useResource<Backups>('/v2/system/backups', canManage);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('');
  const [lastDownload, setLastDownload] = useState<'backup' | 'release'>('backup');
  const pending = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = () => pending.current?.abort();
    window.addEventListener('ontology:scope-cleared', abort);
    return () => {
      abort();
      window.removeEventListener('ontology:scope-cleared', abort);
    };
  }, [session?.user.id, session?.workspace.tenant_id]);
  async function download(kind: 'backup' | 'release') {
    if (busy || !canManage || (kind === 'release' && !canPackage)) return;
    setLastDownload(kind);
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const descriptor =
        kind === 'backup'
          ? await invokeCapability<{ download_url: string; filename: string }>(
              'backup.export',
              {},
              { signal: controller.signal },
            )
          : {
              download_url:
                '/v2/system/release?workspace_id=' + encodeURIComponent(session?.workspace.tenant_id ?? ''),
              filename: copy.admin_source_filename,
            };
      if (controller.signal.aborted) return;
      const blob = await apiBlob(descriptor.download_url, { signal: controller.signal });
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = descriptor.filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage(kind === 'backup' ? copy.admin_export_done : copy.admin_package_done);
    } catch (failure) {
      if (!controller.signal.aborted)
        setError(failure instanceof Error ? failure.message : copy.admin_failure);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return (
    <div className={styles.page} data-admin-ready={!canManage || Boolean(backups.data)}>
      <PageHeader title={copy.admin_backup_title} />
      <section className={styles.section}>
        <h2>{copy.admin_export}</h2>
        <p>{copy.admin_backup_intro}</p>
        <Button type="primary" disabled={!canManage || busy} onClick={() => void download('backup')}>
          {busy ? copy.admin_busy : copy.admin_export}
        </Button>
        {!canManage && <p>{copy.admin_backup_readonly}</p>}
        {message && <p role="status">{message}</p>}
        {error && (
          <StateBlock
            state="error"
            description={error}
            action={{
              label: copy.admin_retry_action,
              onClick: () => void download(lastDownload),
              disabled: busy,
            }}
          />
        )}
      </section>
      <section className={styles.section}>
        <h2>{copy.admin_restore_title}</h2>
        <p>{copy.admin_restore}</p>
        <p>{copy.admin_restore_command}</p>
        <code className={styles.command}>
          python scripts/restore_backup.py backup.zip --tenant-id {session?.workspace.tenant_id} --maintenance
          --reason "{copy.admin_restore_reason}"
        </code>
        <p>{copy.admin_restore_upgrade}</p>
      </section>
      {canManage && (
        <section className={styles.section}>
          <h2>{copy.admin_upgrade_list}</h2>
          {!backups.data ? (
            <ResourceState error={backups.error} reload={backups.reload} />
          ) : backups.data.items.length ? (
            <ul>
              {backups.data.items.map((backup, index) => (
                <li key={`${backup.created_at}:${index}`}>
                  {copy.admin_backup_time}：<span data-visual-dynamic="timestamp">{backup.created_at}</span> ·{' '}
                  {copy.admin_backup_version}：{backup.control_version} · {copy.admin_backup_verified}
                </li>
              ))}
            </ul>
          ) : (
            <StateBlock
              state="empty"
              description={copy.admin_no_backups}
              action={{ label: copy.admin_retry, onClick: backups.reload }}
            />
          )}
        </section>
      )}
      <section className={styles.section}>
        <h2>{copy.admin_windows_title}</h2>
        <p>{copy.admin_windows}</p>
        <a href="https://github.com/min108/ontology-one/releases" target="_blank" rel="noopener noreferrer">
          {copy.admin_release_link}
        </a>
        {canPackage && (
          <>
            <h3>{copy.admin_source_title}</h3>
            <p>{copy.admin_source_intro}</p>
            <Button disabled={busy} onClick={() => void download('release')}>
              {copy.admin_source_export}
            </Button>
          </>
        )}
      </section>
    </div>
  );
}

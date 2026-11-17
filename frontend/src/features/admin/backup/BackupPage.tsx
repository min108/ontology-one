import { useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { apiBlob, invokeCapability } from '../../../api/client';
import { PageLayout } from '../../../ui/PageLayout';
import { PageHeader } from '../../../ui/PageHeader';
import { FoldRow } from '../../../ui/FoldRow';
import { TechnicalDetails } from '../../../ui/TechnicalDetails';
import { StateBlock } from '../../../ui/StateBlock';
import { Timestamp } from '../../identity/common';
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
    <PageLayout
      variant="archive"
      data-admin-ready={!canManage || Boolean(backups.data)}
      header={
        <PageHeader
          title={copy.admin_backup_title}
          lead={copy.r3_admin_backup_lead}
          primaryAction={{
            label: busy ? copy.admin_busy : copy.admin_export,
            disabled: !canManage || busy,
            onClick: () => void download('backup'),
          }}
        />
      }
    >
      <section className={styles.section}>
        <h2>{copy.admin_export}</h2>
        <p>{copy.admin_backup_intro}</p>
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
      <FoldRow title={copy.admin_restore_title} summary={copy.r3_admin_restore_summary}>
        <p>{copy.admin_restore}</p>
        <TechnicalDetails>
          <p>{copy.admin_restore_command}</p>
          <code className={styles.command}>
            python scripts/restore_backup.py backup.zip --tenant-id {session?.workspace.tenant_id}{' '}
            --maintenance --reason "{copy.admin_restore_reason}"
          </code>
        </TechnicalDetails>
        <p>{copy.admin_restore_upgrade}</p>
      </FoldRow>
      {canManage && (
        <section className={styles.section}>
          <h2>{copy.admin_upgrade_list}</h2>
          {!backups.data ? (
            <ResourceState error={backups.error} reload={backups.reload} />
          ) : backups.data.items.length ? (
            <ul>
              {backups.data.items.map((backup, index) => (
                <li key={`${backup.created_at}:${index}`}>
                  {copy.admin_backup_time}：<Timestamp value={backup.created_at} /> ·{' '}
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
      <FoldRow title={copy.admin_windows_title} summary={copy.r3_admin_windows_summary}>
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
      </FoldRow>
    </PageLayout>
  );
}

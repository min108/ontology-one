import { PageLayout } from '../../../ui/PageLayout';
import { PageHeader } from '../../../ui/PageHeader';
import { FoldRow } from '../../../ui/FoldRow';
import { TechnicalDetails } from '../../../ui/TechnicalDetails';
import {
  copy,
  modelLabel,
  ResourceState,
  useResource,
  type ModelStatus,
  type SystemMetadata,
} from '../shared';
import styles from '../Admin.module.css';

export default function SystemPage() {
  const metadata = useResource<SystemMetadata>('/v2/system/metadata');
  const health = useResource<unknown>('/health');
  const model = useResource<ModelStatus>('/v2/system/model');
  return (
    <PageLayout
      variant="archive"
      data-admin-ready={Boolean(metadata.data && health.data && model.data)}
      header={<PageHeader title={copy.admin_system_title} lead={copy.r3_admin_system_lead} />}
    >
      <section className={styles.section}>
        {metadata.data ? (
          <>
            <h2>
              {metadata.data.product_name} {metadata.data.version}
            </h2>
            <p className={styles.english}>{metadata.data.product_name_en}</p>
            <TechnicalDetails>
              <dl className={styles.details}>
                <dt>{copy.admin_run_mode}</dt>
                <dd>
                  {metadata.data.runtime_mode === 'windows' ? copy.admin_windows_mode : copy.admin_server}
                </dd>
                <dt>{copy.admin_control_version}</dt>
                <dd>{metadata.data.control.current}</dd>
                <dt>{copy.admin_workspace_version}</dt>
                <dd>{metadata.data.workspace.current}</dd>
                <dt>{copy.admin_pending}</dt>
                <dd>
                  {[...metadata.data.control.pending, ...metadata.data.workspace.pending].join('、') ||
                    copy.admin_no_pending}
                </dd>
              </dl>
            </TechnicalDetails>
          </>
        ) : (
          <ResourceState error={metadata.error} reload={metadata.reload} />
        )}
      </section>
      <section className={styles.section}>
        <h2>{copy.admin_platform_health}</h2>
        {health.data ? (
          <p>{copy.admin_running}</p>
        ) : (
          <ResourceState error={health.error} reload={health.reload} />
        )}
        <h2>{copy.admin_model_health}</h2>
        {model.data ? (
          <p>{modelLabel(model.data)}</p>
        ) : (
          <ResourceState error={model.error} reload={model.reload} />
        )}
      </section>
      <FoldRow title={copy.admin_run_mode} summary={copy.admin_identity_label}>
        <dl className={styles.details}>
          <dt>{copy.admin_identity_label}</dt>
          <dd>{copy.admin_identity_copy}</dd>
          <dt>{copy.admin_data_label}</dt>
          <dd>{copy.admin_data_copy}</dd>
          <dt>{copy.admin_session_label}</dt>
          <dd>{copy.admin_session_copy}</dd>
          <dt>{copy.admin_audit_label}</dt>
          <dd>{copy.admin_audit_copy}</dd>
        </dl>
      </FoldRow>
    </PageLayout>
  );
}

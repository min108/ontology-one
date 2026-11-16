import { Alert, Button, Table, Tag } from 'antd';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, apiBlob } from '../../api/client';
import { terms } from '../../i18n/zh-CN';
import { PageHeader } from '../../ui/PageHeader';
import { ResourceState, useResource } from '../admin/shared';
import { useBuildIdentity, useLifetime } from '../build/shared';
import styles from '../build/Build.module.css';

interface Source {
  title: string;
  quote: string;
  version: number;
  path: string;
  locator: string;
}
interface Block {
  type: 'paragraph' | 'table';
  text?: string;
  columns?: string[];
  rows?: string[][];
  citations: string[];
}
interface Report {
  id: string;
  title: string;
  source_kind: string;
  current_version: number;
  created_at: string;
  spec: { blocks: Block[]; citations: Record<string, Source>; source_label: string };
  versions: { version: number; created_at: string; change_note: string }[];
}
const copy = terms.labels,
  actions = terms.actions;

export default function ReportsPage() {
  const { id } = useParams(),
    identity = useBuildIdentity();
  return <Reports key={identity.key + ':' + id} id={id} />;
}
function Reports({ id }: { id?: string }) {
  const query = useResource<{ data: Report & { items?: Report[] } }>(
    '/v2/artifacts' + (id ? '/' + encodeURIComponent(id) : ''),
  );
  const [busy, setBusy] = useState(''),
    [error, setError] = useState('');
  const lifetime = useLifetime();
  if (!query.data) return <ResourceState error={query.error} reload={query.reload} />;
  const report = query.data.data;
  async function download(format: 'html' | 'docx', version = report.current_version) {
    setBusy(format);
    setError('');
    try {
      const blob = await apiBlob(`/v2/artifacts/${id}/versions/${version}/files/${format}`, {
        signal: lifetime.current?.signal,
      });
      if (lifetime.current?.signal.aborted) return;
      const url = URL.createObjectURL(blob),
        anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${report.title}-v${version}.${format}`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      if (!lifetime.current?.signal.aborted)
        setError(err instanceof Error ? err.message : copy.r2_report_failed);
    } finally {
      if (!lifetime.current?.signal.aborted) setBusy('');
    }
  }
  async function regenerate() {
    setBusy('again');
    setError('');
    try {
      await api(`/v2/artifacts/${id}:render`, {
        method: 'POST',
        body: JSON.stringify({ artifact_id: id, expected_version: report.current_version }),
        signal: lifetime.current?.signal,
      });
      if (!lifetime.current?.signal.aborted) query.reload();
    } catch (err) {
      if (!lifetime.current?.signal.aborted)
        setError(err instanceof Error ? err.message : copy.r2_report_failed);
    } finally {
      if (!lifetime.current?.signal.aborted) setBusy('');
    }
  }
  return (
    <div className={styles.page}>
      <PageHeader
        title={id ? report.title : copy.r2_reports_title}
        status={<small>{copy.r2_report_private}</small>}
      />
      {error && <Alert type="error" showIcon message={error} />}
      {!id ? (
        <>
          <Link to="/applications">{actions.r2_ask}</Link>
          {!report.items?.length && <p>{copy.r2_report_empty}</p>}
          {report.items?.map((item) => (
            <article className={styles.card} key={item.id}>
              <Link to={'/apply/artifacts/' + item.id}>{item.title}</Link>
              <p>{item.created_at}</p>
              <Tag>{item.source_kind === 'mock' ? copy.r2_report_mock : copy.r2_report_live}</Tag>
            </article>
          ))}
        </>
      ) : (
        <>
          <div className={styles.actions}>
            <Button
              autoInsertSpace={false}
              type="primary"
              onClick={() => void download('docx')}
              loading={busy === 'docx'}
              disabled={!!busy}
            >
              {actions.r2_report_download_word}
            </Button>
            <Button
              autoInsertSpace={false}
              onClick={() => void download('html')}
              loading={busy === 'html'}
              disabled={!!busy}
            >
              {actions.r2_report_download_html}
            </Button>
            <Button
              autoInsertSpace={false}
              onClick={() => void regenerate()}
              loading={busy === 'again'}
              disabled={!!busy}
            >
              {actions.r2_report_again}
            </Button>
            <Link to="/apply/artifacts">{actions.r2_report_back}</Link>
          </div>
          <Tag>{report.spec.source_label}</Tag>
          <article className={styles.card}>
            {report.spec.blocks.map((block, index) => (
              <section key={index}>
                {block.type === 'paragraph' ? (
                  <p className={styles.paragraph}>{block.text}</p>
                ) : (
                  <Table
                    size="small"
                    pagination={false}
                    scroll={{ x: true }}
                    columns={block.columns?.map((title, i) => ({
                      title,
                      dataIndex: String(i),
                      key: String(i),
                    }))}
                    dataSource={block.rows?.map((row, i) => ({
                      key: i,
                      ...Object.fromEntries(row.map((cell, j) => [String(j), cell])),
                    }))}
                  />
                )}
                <p>
                  {block.citations.map((key) => (
                    <a href={'#' + key} key={key}>
                      〔{key}〕{' '}
                    </a>
                  ))}
                </p>
              </section>
            ))}
          </article>
          <section className={styles.stack}>
            <h2>{copy.r2_report_sources}</h2>
            {Object.entries(report.spec.citations).map(([key, source]) => (
              <article className={styles.card} id={key} key={key}>
                <h3>
                  〔{key}〕{source.title}
                </h3>
                <blockquote>{source.quote}</blockquote>
                <p>{source.locator}</p>
                <Link to={source.path}>{actions.r2_preview}</Link>
              </article>
            ))}
          </section>
          <details>
            <summary>{copy.r2_report_versions}</summary>
            {report.versions.map((version) => (
              <p key={version.version}>
                {copy.r2_report_version} {version.version} · {version.created_at}{' '}
                <Button
                  autoInsertSpace={false}
                  disabled={!!busy}
                  onClick={() => void download('docx', version.version)}
                >
                  {actions.r2_report_download_word}
                </Button>
              </p>
            ))}
          </details>
        </>
      )}
    </div>
  );
}

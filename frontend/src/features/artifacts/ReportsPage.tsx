import { Alert, Button, Table } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, apiBlob } from '../../api/client';
import type {
  ArtifactBlock,
  ArtifactListResult,
  ArtifactRender,
  ArtifactResult,
  ArtifactSource,
} from '../../api/generated/types';
import { terms } from '../../i18n/zh-CN';
import { PageLayout } from '../../ui/PageLayout';
import { PageHeader } from '../../ui/PageHeader';
import { EvidenceDrawer } from '../../ui/EvidenceDrawer';
import { SourceViewer } from '../../ui/SourceViewer';
import { FoldRow } from '../../ui/FoldRow';
import { NextStepBar } from '../../ui/NextStepBar';
import { OriginTag } from '../../ui/OriginTag';
import { StateBlock } from '../../ui/StateBlock';
import { TechnicalDetails } from '../../ui/TechnicalDetails';
import { formatDateTime } from '../../ui/formatDateTime';
import { ResourceState } from '../admin/shared';
import { useBuildIdentity, useLifetime } from '../build/shared';
import styles from './Reports.module.css';

const copy = terms.labels,
  actions = terms.actions;

/** Render business labels while keeping original responses intact in saved provenance. */
function businessText(text: string, sources: Record<string, ArtifactSource>) {
  let value = text
    .replace(/\[([^\]]+)\]\([^\s)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1');
  for (const [key, source] of Object.entries(sources)) {
    value = value.replaceAll('[' + key + ']', '').replaceAll('〔' + key + '〕', '');
    for (const identifier of [source.entity_id, source.material_id, source.anchor_id])
      if (typeof identifier === 'string' && identifier) value = value.replaceAll(identifier, source.title);
  }
  return value;
}

export default function ReportsPage() {
  const { id } = useParams(),
    identity = useBuildIdentity();
  return <Reports key={identity.key + ':' + id} id={id} identity={identity.key} />;
}
function Reports({ id, identity }: { id?: string; identity: string }) {
  const query = useQuery<ArtifactResult | ArtifactListResult>({
    queryKey: ['reports', identity, id],
    queryFn: ({ signal }) =>
      id
        ? api<ArtifactResult>('/v2/artifacts/' + encodeURIComponent(id), { signal })
        : api<ArtifactListResult>('/v2/artifacts', { signal }),
  });
  const [busy, setBusy] = useState(''),
    [error, setError] = useState(''),
    [sourceKey, setSourceKey] = useState<string>();
  const lifetime = useLifetime(),
    navigate = useNavigate();
  const report = id ? (query.data as ArtifactResult | undefined)?.data : undefined;
  const items = id ? [] : ((query.data as ArtifactListResult | undefined)?.data.items ?? []);
  async function download(format: 'html' | 'docx', version = report?.current_version) {
    if (!report || !version || busy) return;
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
    if (!report || !id || busy) return;
    setBusy('again');
    setError('');
    try {
      const body = { artifact_id: id, expected_version: report.current_version } satisfies ArtifactRender;
      await api<ArtifactResult>(`/v2/artifacts/${id}:render`, {
        method: 'POST',
        body: JSON.stringify(body),
        signal: lifetime.current?.signal,
      });
      if (!lifetime.current?.signal.aborted) await query.refetch();
    } catch (err) {
      if (!lifetime.current?.signal.aborted)
        setError(err instanceof Error ? err.message : copy.r2_report_failed);
    } finally {
      if (!lifetime.current?.signal.aborted) setBusy('');
    }
  }
  const source = sourceKey && report?.spec.citations[sourceKey];
  const citations = report ? Object.keys(report.spec.citations) : [];
  const preview =
    report?.spec.blocks.find((item) => item.type === 'paragraph' && item.citations.length) ??
    report?.spec.blocks.find((item) => item.type === 'paragraph');
  const cite = (keys: string[]) =>
    keys.map((key) => (
      <sup key={key}>
        <button
          className={styles.citation}
          aria-label={`${actions.r3_report_source} ${citations.indexOf(key) + 1}`}
          onClick={() => setSourceKey(key)}
        >
          {citations.indexOf(key) + 1}
        </button>
      </sup>
    ));
  const block = (item: ArtifactBlock, index: number) => (
    <section key={index}>
      {item.type === 'paragraph' ? (
        <p className={styles.paragraph}>
          {businessText(item.text ?? '', report!.spec.citations)} {cite(item.citations)}
        </p>
      ) : (
        <>
          <Table
            pagination={false}
            size="small"
            scroll={{ x: true }}
            columns={item.columns?.map((title, i) => ({
              title: businessText(title, report!.spec.citations),
              dataIndex: String(i),
              key: String(i),
            }))}
            dataSource={item.rows?.map((row, i) => ({
              key: i,
              ...Object.fromEntries(
                row.map((cell, j) => [String(j), businessText(cell, report!.spec.citations)]),
              ),
            }))}
          />
          <p>{cite(item.citations)}</p>
        </>
      )}
    </section>
  );
  const header = (
    <PageHeader
      title={report?.title ?? copy.r2_reports_title}
      back={id ? { label: actions.r2_report_back, href: '/apply/artifacts' } : undefined}
      lead={report ? copy.r3_report_complete : copy.r2_report_private}
      status={report && <OriginTag origin={report.source_kind === 'mock' ? 'mock' : 'ai'} />}
      primaryAction={
        report
          ? {
              label: actions.r2_report_download_word,
              onClick: () => void download('docx'),
              disabled: !!busy,
              loading: busy === 'docx',
            }
          : !id
            ? { label: actions.r2_ask, onClick: () => navigate('/applications') }
            : undefined
      }
      secondaryAction={
        report
          ? {
              label: actions.r2_report_download_html,
              onClick: () => void download('html'),
              disabled: !!busy,
              loading: busy === 'html',
            }
          : undefined
      }
    />
  );
  return (
    <PageLayout header={header} variant={id ? 'archive' : 'list'}>
      {(error || (query.data && query.error)) && (
        <Alert type="error" showIcon title={error || query.error?.message} />
      )}
      {!query.data ? (
        <ResourceState error={query.error?.message} reload={() => void query.refetch()} />
      ) : report ? (
        <>
          <article className={styles.card}>
            <p className={styles.caption}>
              {copy.r2_report_created} · {formatDateTime(report.created_at)}
            </p>
            {report.spec.revision && <p>{copy.r3_report_revised}</p>}
            <p className={styles.paragraph}>
              {businessText(preview?.text ?? report.title, report.spec.citations).slice(0, 220)}{' '}
              {cite(preview?.citations ?? [])}
            </p>
          </article>
          <div>
            <FoldRow title={copy.r3_report_body} summary={copy.r3_report_body_summary}>
              {report.spec.blocks.map(block)}
            </FoldRow>
            <FoldRow title={copy.r2_report_sources} summary={copy.r3_report_sources_summary}>
              {Object.entries(report.spec.citations).map(([key, item]) => (
                <p key={key}>
                  <Button type="text" onClick={() => setSourceKey(key)}>
                    {item.source_title || item.title}
                  </Button>
                </p>
              ))}
            </FoldRow>
            {report.spec.revision && (
              <FoldRow title={actions.r3_report_compare} summary={copy.r3_report_revised}>
                <h3>{copy.r3_report_before}</h3>
                <p className={styles.paragraph}>
                  {businessText(report.spec.revision.before, report.spec.citations)}
                </p>
                <h3>{copy.r3_report_after}</h3>
                <p className={styles.paragraph}>
                  {businessText(report.spec.revision.after, report.spec.citations)}
                </p>
              </FoldRow>
            )}
            <FoldRow title={copy.r2_report_versions} summary={copy.r3_report_versions_summary}>
              <Button disabled={!!busy} loading={busy === 'again'} onClick={() => void regenerate()}>
                {actions.r2_report_again}
              </Button>
              {report.versions.map((version) => (
                <p key={version.version}>
                  {copy.r2_report_version} {version.version} · {formatDateTime(version.created_at)}{' '}
                  <Button
                    type="text"
                    disabled={!!busy}
                    onClick={() => void download('docx', version.version)}
                  >
                    {actions.r2_report_download_word}
                  </Button>
                </p>
              ))}
            </FoldRow>
            <TechnicalDetails>
              <pre>{JSON.stringify({ id: report.id, version: report.current_version }, null, 2)}</pre>
            </TechnicalDetails>
          </div>
          <NextStepBar current={5} complete description={copy.r3_report_complete} />
        </>
      ) : items.length ? (
        <Table
          rowKey="id"
          pagination={{ pageSize: 15 }}
          dataSource={items}
          onRow={() => ({ 'data-first-row': true }) as React.HTMLAttributes<HTMLTableRowElement>}
          columns={[
            {
              title: copy.r2_report_title,
              dataIndex: 'title',
              render: (title: string, item) => <Link to={'/apply/artifacts/' + item.id}>{title}</Link>,
            },
            {
              title: copy.r3_report_origin,
              dataIndex: 'source_kind',
              render: (kind: string) => <OriginTag origin={kind === 'mock' ? 'mock' : 'ai'} />,
            },
            {
              title: copy.r2_report_created,
              dataIndex: 'created_at',
              render: (value: string) => formatDateTime(value),
            },
          ]}
        />
      ) : (
        <StateBlock
          state="empty"
          description={copy.r2_report_empty}
          action={{ label: actions.r2_ask, onClick: () => navigate('/applications') }}
        />
      )}
      <EvidenceDrawer
        open={Boolean(source)}
        onClose={() => setSourceKey(undefined)}
        title={source ? source.source_title || source.title : undefined}
        footer={source && <Link to={source.path}>{actions.r3_report_open_material}</Link>}
      >
        {source && (
          <>
            <h3>{source.title}</h3>
            <SourceViewer text={source.quote} quote={source.quote} />
            <TechnicalDetails>
              <p>{source.locator}</p>
              <pre>{JSON.stringify({ version: source.version, anchor: source.anchor_id }, null, 2)}</pre>
            </TechnicalDetails>
          </>
        )}
      </EvidenceDrawer>
    </PageLayout>
  );
}

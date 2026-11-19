import { useQuery } from '@tanstack/react-query';
import { Button } from 'antd';
import { useLocation, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api/client';
import type { MaterialSourcePage } from '../../api/generated/types';
import { workspaceQueryKey } from '../../api/query';
import { useScopeStore } from '../../state/scope';
import { useSessionStore } from '../../state/session';
import { terms } from '../../i18n/zh-CN';
import { PageHeader, PageLayout, SourceViewer, StatusTag } from '../../ui';
import { ResourceState } from '../admin/shared';
import styles from '../build/Build.module.css';

const copy = terms.labels;
const pageSize = 4096;

export function MaterialReading({
  id,
  params,
  onPage,
  inline = false,
}: {
  id: string;
  params: URLSearchParams;
  onPage: (offset: number, sourceVersion: string) => void;
  inline?: boolean;
}) {
  const { session, revision, switching, permission_keys } = useSessionStore();
  const { scopeKey, tenantId } = useScopeStore();
  const location = useLocation();
  const selectors = new URLSearchParams({ offset: params.get('offset') ?? '0' });
  for (const [linkKey, apiKey] of [
    ['chunk', 'chunk_id'],
    ['anchor', 'anchor_id'],
    ['source_version', 'source_version'],
    ['citing_entity_id', 'citing_entity_id'],
    ['citing_relation_id', 'citing_relation_id'],
  ]) {
    const value = params.get(linkKey);
    if (value) selectors.set(apiKey, value);
  }
  const path = '/v2/materials/' + encodeURIComponent(id) + '/source?' + selectors;
  const sourceVersion = selectors.get('source_version');
  const ready = Boolean(session && !switching && tenantId === session.workspace.tenant_id);
  const query = useQuery({
    queryKey: workspaceQueryKey(
      session?.workspace.tenant_id ?? 'unavailable',
      scopeKey,
      'material-source',
      session?.user.id,
      session?.user.role,
      permission_keys,
      revision,
      location.key,
      path,
    ),
    queryFn: async ({ signal }) => {
      const page = await api<MaterialSourcePage>(path, { signal });
      if (page.material_id !== id || (sourceVersion && page.source_version !== sourceVersion))
        throw new Error(copy.client_response_invalid);
      return page;
    },
    enabled: ready,
    gcTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: 'always',
    retry: false,
  });
  // A previously authorized page must not survive a permission check or replace a denied response.
  const page =
    ready && query.isSuccess && query.isFetchedAfterMount && query.fetchStatus === 'idle'
      ? query.data
      : undefined;
  const anchored = selectors.has('chunk_id') || selectors.has('anchor_id');
  const length = Array.from(page?.text ?? '').length;
  const body = page ? (
    <section
      className={`${styles.card} ${styles.stack}`}
      aria-label={anchored ? copy.global_search_source_chunk : copy.r2_original}
    >
      <p>
        {page.locator}
        {(page.version !== null || page.source_version) && (
          <>
            {' '}
            · {copy.r3_ui_source_version} {page.version ?? page.source_version}
          </>
        )}
      </p>
      {(page.offset > 0 || page.offset + length < page.total) && (
        <div className={styles.actions}>
          <Button
            disabled={page.offset === 0}
            onClick={() => onPage(Math.max(0, page.offset - pageSize), page.source_version)}
          >
            {terms.actions.identity_previous}
          </Button>
          <span>
            {Math.min(Math.floor(page.offset / pageSize) + 1, Math.ceil(page.total / pageSize))} /{' '}
            {Math.ceil(page.total / pageSize)}
          </span>
          <Button
            disabled={length === 0 || page.offset + length >= page.total}
            onClick={() => onPage(page.offset + length, page.source_version)}
          >
            {terms.actions.identity_next}
          </Button>
        </div>
      )}
      <SourceViewer text={page.text} quote={anchored ? page.text : ''} start={0} end={length} />
    </section>
  ) : (
    <ResourceState
      error={ready && query.error ? query.error.message : undefined}
      reload={() => {
        if (ready) void query.refetch();
      }}
    />
  );
  return inline ? (
    body
  ) : (
    <PageLayout
      variant="archive"
      header={
        <PageHeader
          title={page?.title ?? copy.r2_original}
          status={page && <StatusTag status={page.status} />}
        />
      }
    >
      {body}
    </PageLayout>
  );
}

export default function MaterialReaderPage() {
  const { id = '' } = useParams();
  const [params, setParams] = useSearchParams();
  return (
    <MaterialReading
      id={id}
      params={params}
      onPage={(offset, sourceVersion) => {
        const next = new URLSearchParams(params);
        next.set('offset', String(offset));
        if (sourceVersion) next.set('source_version', sourceVersion);
        setParams(next);
      }}
    />
  );
}

import { lazy } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Navigate, useLocation, useParams } from 'react-router-dom';
import { useAuth } from '@legacy/auth';
import { entityHref } from '@legacy/domain';
import { Loading, ErrorPanel } from '@legacy/components/ui';
import { readEntityRoute } from '../api/shell';
import { workspaceQueryKey } from '../api/query';
import { useScopeStore } from '../state/scope';
import { terms } from '../i18n/zh-CN';

const AssetDetail = lazy(() => import('@legacy/pages/AssetDetail'));

export function EntityDossierRoute() {
  const { id = '' } = useParams();
  const location = useLocation();
  const { session } = useAuth();
  const scopeKey = useScopeStore((state) => state.scopeKey);
  const query = useQuery({
    queryKey: workspaceQueryKey(session?.workspace.tenant_id ?? 'unavailable', scopeKey, 'entity-route', id),
    queryFn: ({ signal }) => readEntityRoute(id, signal),
    enabled: Boolean(session && id),
  });
  if (query.isPending) return <Loading />;
  if (query.error)
    return (
      <ErrorPanel
        error={query.error instanceof Error ? query.error.message : terms.states.error.description}
        reload={() => void query.refetch()}
      />
    );
  const data = query.data;
  if (data.kind === 'person') return <Navigate replace to={'/authority?person=' + encodeURIComponent(id)} />;
  if (['project', 'task', 'collection'].includes(data.kind)) {
    const prefix =
      data.kind === 'project'
        ? '/projects/'
        : data.kind === 'collection'
          ? '/knowledge/collections/'
          : '/tasks/';
    return <Navigate replace to={prefix + encodeURIComponent(id) + location.search + location.hash} />;
  }
  if (['material', 'dataset'].includes(data.kind))
    return <Navigate replace to={entityHref(id, data.kind) + location.search + location.hash} />;
  if (data.project_id && ['matter', 'evidence'].includes(data.kind)) {
    const params = new URLSearchParams(location.search);
    params.set('panel', 'matters');
    params.set('matter', data.kind === 'matter' ? id : (data.matter_id ?? ''));
    if (data.kind === 'evidence') params.set('evidence', id);
    return <Navigate replace to={'/projects/' + encodeURIComponent(data.project_id) + '?' + params} />;
  }
  return <AssetDetail />;
}

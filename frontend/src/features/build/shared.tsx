import { useEffect, useRef } from 'react';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
import { terms } from '../../i18n/zh-CN';
import { StateBlock, PageLayout, PageHeader } from '../../ui';

export function BuildForbidden({ title = terms.labels.r2_changeset_title }: { title?: string }) {
  return (
    <PageLayout variant="list" header={<PageHeader title={title} />}>
      <StateBlock
        state="forbidden"
        action={{ label: terms.actions.shell_back_home, onClick: () => window.location.assign('/') }}
      />
    </PageLayout>
  );
}

export const copy = terms.labels;
export const actions = terms.actions;
export type {
  Evidence,
  Proposal,
  Recipe,
  Review,
  ProposalItem as Item,
  CandidatePatch as Candidate,
} from '../../api/generated/types';
import type { Evidence } from '../../api/generated/types';
export function sourceParams(ref: Evidence, citingEntityId?: string) {
  const params = new URLSearchParams({ anchor: ref.anchor_id, source_version: ref.source_version });
  if (citingEntityId) {
    const relation = ref.citing_relation_id || ref.relation_id;
    if (typeof relation === 'string' && relation) params.set('citing_relation_id', relation);
    else params.set('citing_entity_id', citingEntityId);
  }
  return params;
}

export const sourceHref = (ref: Evidence, citingEntityId?: string) =>
  '/materials/' + encodeURIComponent(ref.material_id) + '?' + sourceParams(ref, citingEntityId);

export function useBuildIdentity() {
  const session = useSessionStore((state) => state.session);
  const scopeKey = useScopeStore((state) => state.scopeKey);
  return {
    key: JSON.stringify([session?.user.id, session?.workspace.tenant_id, scopeKey]),
    canEdit: Boolean(session?.permissions.can_edit && !session.workspace.readonly),
    canRead: Boolean(session && ['admin', 'modeler', 'reviewer'].includes(session.user.role)),
  };
}
export function useLifetime() {
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const owned = new AbortController();
    controller.current = owned;
    const clear = () => owned.abort();
    window.addEventListener('ontology:scope-cleared', clear);
    return () => {
      owned.abort();
      window.removeEventListener('ontology:scope-cleared', clear);
    };
  }, []);
  return controller;
}

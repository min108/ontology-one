import { useEffect, useRef } from 'react';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
import { terms } from '../../i18n/zh-CN';
import { StateBlock } from '../../ui/StateBlock';

export function BuildForbidden() {
  return (
    <StateBlock
      state="forbidden"
      action={{ label: terms.actions.shell_back_home, onClick: () => window.location.assign('/') }}
    />
  );
}

export const copy = terms.labels;
export const actions = terms.actions;
export interface Entity {
  id: string;
  title: string;
  summary: string;
  kind: string;
  status: string;
  version: number;
  payload: { content?: string; definition?: string; conditions?: string[]; source_refs?: Evidence[] };
}
export interface Evidence {
  material_id: string;
  anchor_id: string;
  source_version: string;
  quote: string;
  start: number;
  end: number;
  locator: string;
}
export interface Candidate {
  kind: string;
  title: string;
  content: string;
}
export interface Item {
  id: string;
  tier: 'sure' | 'check' | 'doubt';
  decision: string;
  one_liner: string;
  rationale: string;
  content_origin: string;
  after_data: Candidate;
  evidence: Evidence[];
  validation: { level: string; issues: string[] };
  duplicate_of?: string;
  applied_entity_id?: string;
}
export interface Proposal {
  can_submit: boolean;
  id: string;
  title: string;
  summary: string;
  status: string;
  items: Item[];
  source_text: string;
  source_material_id: string;
}
export interface Recipe {
  id: string;
  title: string;
  status: string;
  stage_message: string;
  changeset_id?: string;
  progress?: { done: number; total: number };
  used_model: number;
}
export interface Review {
  entity: Entity;
  submitter: string;
  submitted_at: string;
  can_decide: boolean;
  blocked_reason: string;
  source_refs: Evidence[];
}
export const sourceHref = (ref: Evidence) =>
  '/materials/' +
  encodeURIComponent(ref.material_id) +
  '?' +
  new URLSearchParams({ anchor: ref.anchor_id, source_version: ref.source_version });

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

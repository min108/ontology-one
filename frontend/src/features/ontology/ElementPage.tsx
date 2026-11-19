import { Alert, Button, Descriptions } from 'antd';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { terms } from '../../i18n/zh-CN';
import { invokeCapability } from '../../api/client';
import { RuleEditor } from './RuleEditor';
import { RuleTests } from './RuleTests';
import { ElementRepair } from './ElementRepair';
import { aliasesOf, ElementAliases } from './ElementAliases';
import { GovernanceFacts } from '../build/GovernanceFacts';
import { useUiStore } from '../../state/ui';
import {
  PageHeader,
  PageLayout,
  FoldRow,
  TechnicalDetails,
  KindTag,
  StatusTag,
  formatDateTime,
  NextStepBar,
  OriginTag,
  SourceViewer,
} from '../../ui';
import { businessText, fieldLabels, sourceDescription } from '../build/businessText';
import { SourceEvidence } from '../build/SourceEvidence';
import { askHref, sentence, isAnchoredEvidence } from '../build/model';
import { ResourceState, useResource } from '../admin/shared';
import { GateChecklist } from '../build/GateChecklist';
import { type Evidence, useBuildIdentity, useLifetime } from '../build/shared';
import styles from '../build/Build.module.css';

const copy = terms.labels;
const activityLabels: Record<string, string> = {
  create: terms.actions.catalog_2_0,
  update: terms.actions.catalog_1_1,
  edit: terms.actions.catalog_1_1,
  save: terms.actions.catalog_2_0,
  submit: terms.actions.submit,
  withdraw: terms.actions.catalog_2_2,
  approve: terms.actions.catalog_2_3,
  publish: terms.actions.catalog_2_4,
  return: terms.actions.catalog_2_6,
  revise: terms.actions.catalog_2_8,
  archive: terms.actions.catalog_2_9,
  restore: terms.actions.catalog_2_10,
  complete: terms.actions.catalog_3_4,
  gate_waiver: copy.r4_gate_waiver,
  quality_waiver: copy.r4_gate_waiver,
  gate_waiver_application: copy.r4a1_legacy_application_action,
};
function activityLabel(action: string): string {
  if (Object.hasOwn(activityLabels, action)) return activityLabels[action];
  // Older records already carry a business-facing Chinese action name.
  return /^[\p{Script=Han}\s、，。（）]+$/u.test(action) ? action : '';
}
export type { ElementDetail } from '../../api/generated/types';
import type {
  ElementDetail,
  ElementDetailResult,
  ElementUpdate,
  TraceResult,
} from '../../api/generated/types';

function MaterialPassage({ id, params }: { id: string; params: URLSearchParams }) {
  const target = new URLSearchParams();
  for (const [linkKey, apiKey] of [
    ['chunk', 'chunk_id'],
    ['anchor', 'anchor_id'],
    ['source_version', 'source_version'],
  ]) {
    const value = params.get(linkKey);
    if (value) target.set(apiKey, value);
  }
  const query = useResource<TraceResult>('/v2/elements/' + encodeURIComponent(id) + '/passage?' + target);
  if (query.error || !query.data) return <ResourceState error={query.error} reload={query.reload} />;
  const passage = query.data.sources[0];
  if (!passage) return <p>{copy.r3_main_no_source}</p>;
  return (
    <section aria-label={copy.global_search_source_chunk}>
      <h2>{copy.global_search_source_chunk}</h2>
      <p>
        {passage.locator} · {copy.r2_card_versions} {passage.version}
      </p>
      <SourceViewer
        text={passage.quote}
        quote={passage.quote}
        start={0}
        end={Array.from(passage.quote).length}
      />
    </section>
  );
}
export function BusinessCard({ detail }: { detail: ElementDetail }) {
  const [source, setSource] = useState<Evidence>();
  const aliases = aliasesOf(detail.entity);
  const sources = Array.isArray(detail.entity.payload.source_refs)
    ? detail.entity.payload.source_refs.filter(
        (value) => sourceDescription(value) || isAnchoredEvidence(value),
      )
    : [];
  return (
    <article className={styles.card} aria-label={detail.entity.title}>
      <p className={styles.statement}>{detail.card.one_liner}</p>
      {aliases.length > 0 && (
        <p>
          {copy.r4b_aliases} · {aliases.join('、')}
        </p>
      )}
      {(detail.entity.payload.extraction_mode === 'online' ||
        detail.entity.payload.extraction_mode === 'local') && (
        <OriginTag
          origin={detail.entity.payload.extraction_mode === 'online' ? 'ai' : 'local'}
          reviewed={detail.entity.status === 'published'}
        />
      )}
      {detail.card.points.length > 0 && (
        <>
          <h2>{copy.r2_card_points}</h2>
          <ul>
            {detail.card.points.slice(0, 3).map((point, i) => (
              <li key={i}>{point}</li>
            ))}
          </ul>
        </>
      )}
      <h2>{copy.r2_card_sources}</h2>
      {!sources.length && <p>{copy.r2_card_empty}</p>}
      {sources.slice(0, 1).map((ref, i) => (
        <blockquote className={styles.quote} key={i}>
          <p>{sourceDescription(ref) || copy.r3_main_no_source}</p>
          {isAnchoredEvidence(ref) && (
            <Button type="text" autoInsertSpace={false} onClick={() => setSource(ref)}>
              {copy.r3_main_view_original}
            </Button>
          )}
        </blockquote>
      ))}
      {sources.length > 1 && (
        <FoldRow
          title={copy.r31_other_sources}
          summary={sentence(copy.r3_main_count, { count: sources.length - 1 })}
        >
          {sources.slice(1).map((ref, i) => (
            <blockquote className={styles.quote} key={i}>
              <p>{sourceDescription(ref) || copy.r3_main_no_source}</p>
              {isAnchoredEvidence(ref) && (
                <Button type="text" onClick={() => setSource(ref)}>
                  {copy.r3_main_view_original}
                </Button>
              )}
            </blockquote>
          ))}
        </FoldRow>
      )}
      <SourceEvidence source={source} onClose={() => setSource(undefined)} />
    </article>
  );
}
export default function ElementPage() {
  const { id = '' } = useParams(),
    identity = useBuildIdentity();
  return <Element key={identity.key + id} id={id} />;
}
function openRepair() {
  const panel = document.getElementById('element-repair');
  panel?.scrollIntoView({ block: 'start' });
  panel?.focus({ preventScroll: true });
}
function Element({ id }: { id: string }) {
  const lifetime = useLifetime();
  const viewMode = useUiStore((state) => state.viewMode);
  const navigate = useNavigate();
  const [notice, setNotice] = useState('');
  const [params] = useSearchParams();
  const from = params.get('from') || '';
  const fromChangeset = /^\/build\/changesets\/[^/?#]+$/.test(from);
  const back = fromChangeset
    ? { label: copy.r2_changeset_title, href: from }
    : { label: terms.actions.r2_card_back, href: '/ontology/catalog' };
  const query = useResource<ElementDetailResult>('/v2/elements/' + encodeURIComponent(id));
  const canRepair =
    !!query.data?.data.can_edit &&
    ['draft', 'returned'].includes(query.data.data.entity.status) &&
    !!query.data.data.gates?.checks.some((check) => check.status !== 'not_applicable');
  const repairRequested = params.get('repair') === '1';
  useEffect(() => {
    if (repairRequested && canRepair) openRepair();
  }, [id, repairRequested, canRepair]);
  if (!query.data)
    return (
      <PageLayout variant="archive" header={<PageHeader title={copy.r2_catalog_title} />}>
        <ResourceState error={query.error} reload={query.reload} />
      </PageLayout>
    );
  const detail = query.data.data,
    entity = detail.entity,
    professional = viewMode === 'professional';
  const fields = Object.entries(fieldLabels).flatMap(([key, label]) => {
    const text = businessText(entity.payload[key]);
    return text ? [{ key, label, children: <span className={styles.paragraph}>{text}</span> }] : [];
  });
  const conditions = fields.filter((field) => ['scope', 'conditions', 'constraints'].includes(field.key));
  const activities = (entity.activities || []).flatMap((event) => {
    const label = activityLabel(event.action);
    return label ? [{ ...event, label }] : [];
  });
  return (
    <PageLayout
      variant="archive"
      header={
        <PageHeader
          title={entity.title}
          back={back}
          status={
            <>
              <KindTag kind={entity.kind} />
              <StatusTag status={entity.status} />
              {professional && (
                <span>
                  v{entity.version} · {entity.domain}
                </span>
              )}
            </>
          }
          extra={
            <ElementAliases
              detail={detail}
              disabled={!!query.refreshing || !!query.error}
              onSaved={(message) => {
                setNotice(message ?? '');
                query.reload();
              }}
            />
          }
          primaryAction={
            entity.status === 'published'
              ? { label: copy.r3_main_ask, onClick: () => navigate(askHref(id)) }
              : detail.can_edit
                ? {
                    label: copy.r3_main_edit_draft,
                    onClick: canRepair ? openRepair : () => navigate('/assets/' + encodeURIComponent(id)),
                  }
                : undefined
          }
        />
      }
    >
      {notice && <Alert type="success" role="status" title={notice} />}
      <div className={styles['archive-columns']}>
        <div className={styles.stack}>
          {entity.kind === 'material' && (params.get('chunk') || params.get('anchor')) && (
            <MaterialPassage id={id} params={params} />
          )}
          <BusinessCard detail={detail} />
          <GovernanceFacts gates={detail.gates} />
          <div className={styles.folds}>
            {conditions.length > 0 && (
              <FoldRow
                title={copy.r3_main_conditions}
                summary={sentence(copy.r3_main_count, { count: conditions.length })}
              >
                <div role="region" aria-label={copy.r3_main_conditions}>
                  <Descriptions column={1} items={conditions} />
                </div>
              </FoldRow>
            )}
            {detail.card.relations.length > 0 && (
              <FoldRow
                title={copy.r2_card_relations}
                summary={sentence(copy.r3_main_relation_summary, { count: detail.card.relations.length })}
              >
                <div role="region" aria-label={copy.r2_card_relations}>
                  <ul>
                    {detail.card.relations.map((relation, i) => (
                      <li key={i}>{relation}</li>
                    ))}
                  </ul>
                </div>
              </FoldRow>
            )}
            {detail.versions.length > 0 && (
              <FoldRow
                title={copy.r2_card_versions}
                summary={sentence(copy.r3_main_version_summary, { count: detail.versions.length })}
              >
                <div role="region" aria-label={copy.r2_card_versions}>
                  <ul>
                    {detail.versions.map((version) => (
                      <li key={version.id}>
                        v{version.version} · {formatDateTime(version.created_at)} ·{' '}
                        {version.reason || version.change_note}
                      </li>
                    ))}
                  </ul>
                </div>
              </FoldRow>
            )}
            {activities.length > 0 && (
              <FoldRow
                title={copy.r2_card_activity}
                summary={sentence(copy.r3_main_activity_summary, { count: activities.length })}
              >
                <div role="region" aria-label={copy.r2_card_activity}>
                  <ul>
                    {activities.map((event) => (
                      <li key={event.id}>
                        {event.label}
                        {event.detail ? ` · ${event.detail}` : ''} · {formatDateTime(event.created_at)}
                      </li>
                    ))}
                  </ul>
                </div>
              </FoldRow>
            )}
            {professional && (
              <>
                {fields.length > 0 && (
                  <FoldRow
                    title={copy.r2_card_structure}
                    summary={sentence(copy.r3_main_count, { count: fields.length })}
                    defaultOpen
                  >
                    <div role="region" aria-label={copy.r2_card_structure}>
                      <Descriptions column={1} items={fields} />
                    </div>
                  </FoldRow>
                )}
              </>
            )}
            <GateChecklist
              gates={detail.gates}
              professional={professional}
              onRepair={canRepair ? openRepair : undefined}
            />
            {canRepair && (
              <ElementRepair
                key={`repair:${entity.id}:${entity.version}`}
                detail={detail}
                disabled={!!query.refreshing || !!query.error}
                onSaved={(message) => {
                  setNotice(message);
                  query.reload();
                }}
              />
            )}
            {professional && ['rule', 'metric'].includes(entity.kind) && (
              <RuleTests
                key={`tests:${entity.id}:${entity.version}`}
                id={id}
                disabled={!!query.refreshing || !!query.error}
                onComplete={query.reload}
              />
            )}
            <TechnicalDetails>
              {detail.can_edit &&
                ['rule', 'metric'].includes(entity.kind) &&
                ['draft', 'returned'].includes(entity.status) && (
                  <RuleEditor
                    key={entity.id + ':' + entity.version}
                    entity={entity}
                    disabled={!!query.refreshing || !!query.error}
                    onSave={async (fields, sentenceConfirmed) => {
                      await invokeCapability<ElementDetailResult>(
                        'element.update_draft',
                        {
                          entity_id: id,
                          version: entity.version,
                          payload_fields: fields,
                          sentence_confirmed: sentenceConfirmed,
                        } satisfies ElementUpdate,
                        { signal: lifetime.current?.signal },
                      );
                      if (!lifetime.current?.signal.aborted) query.reload();
                    }}
                  />
                )}
              <p>{entity.id}</p>
              <pre>{JSON.stringify(entity.payload, null, 2)}</pre>
            </TechnicalDetails>
          </div>
        </div>
        <aside className={`${styles.card} ${styles.stack}`}>
          <h2>{copy.r2_card_usage}</h2>
          <p>{detail.card.usage}</p>
          {!fromChangeset && (
            <>
              <h2>{copy.r3_main_next}</h2>
              <p>{entity.status === 'published' ? copy.r31_element_next_ask : copy.r3_main_draft_note}</p>
            </>
          )}
          {detail.can_edit && entity.status === 'published' && (
            <Link to={'/assets/' + encodeURIComponent(id)}>{terms.actions.r2_card_edit}</Link>
          )}
          {entity.status === 'review' && (
            <>
              <p>{copy.r4a1_legacy_sod}</p>
              <Link to="/govern/reviews">{copy.r4a1_legacy_review_link}</Link>
            </>
          )}
        </aside>
      </div>
      {fromChangeset && (
        <NextStepBar
          current={entity.status === 'published' ? 4 : 2}
          description={entity.status === 'published' ? copy.r3_main_next_ask : copy.r3_main_next_submit}
          onStep={(step) => navigate(step === 1 ? '/build/intake' : step === 2 ? from : '/govern/reviews')}
        />
      )}
    </PageLayout>
  );
}

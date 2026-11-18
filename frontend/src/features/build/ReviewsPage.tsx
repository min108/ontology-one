import { Alert, Button, Input, Modal, Select } from 'antd';
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { ReviewBody, ReviewListResult, ReviewResult } from '../../api/generated/types';
import {
  PageHeader,
  PageLayout,
  SplitView,
  NextStepBar,
  FoldRow,
  KindTag,
  StatusTag,
  TechnicalDetails,
  OriginTag,
  formatDateTime,
  StateBlock,
} from '../../ui';
import { terms } from '../../i18n/zh-CN';
import { useUiStore } from '../../state/ui';
import { ResourceState, useResource } from '../admin/shared';
import { actions, copy, type Evidence, useBuildIdentity, useLifetime, BuildForbidden } from './shared';
import { askHref, sentence } from './model';
import { SourceEvidence } from './SourceEvidence';
import { businessText, sourceDescription } from './businessText';
import { isAnchoredEvidence } from './model';
import { GateChecklist, gateBlockers, type GateWaivers } from './GateChecklist';
import { GovernanceFacts } from './GovernanceFacts';
import styles from './Build.module.css';

export default function ReviewsPage() {
  const identity = useBuildIdentity();
  return identity.canRead ? <Inbox key={identity.key} /> : <BuildForbidden title={copy.r2_reviews_title} />;
}
function Inbox() {
  const [kind, setKind] = useState(''),
    [selected, setSelected] = useState('');
  const list = useResource<ReviewListResult>('/v2/reviews?kind=' + kind);
  const professional = useUiStore((state) => state.viewMode === 'professional');
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const [reason, setReason] = useState(''),
    [confirmation, setConfirmation] = useState<{
      id: string;
      version: number;
      title: string;
      decision: ReviewBody['decision'];
    }>();
  const [result, setResult] = useState<{
    id: string;
    title: string;
    decision: ReviewBody['decision'];
    reason: string;
  }>();
  const [waivers, setWaivers] = useState<GateWaivers>({});
  const [denials, setDenials] = useState<GateWaivers>({});
  const [focus, setFocus] = useState<Evidence>();
  const lifetime = useLifetime(),
    navigate = useNavigate();
  const rows = list.data?.data.items;
  const current = rows?.find((row) => row.entity.id === selected);
  useEffect(() => {
    if (!confirmation && rows && !rows.some((row) => row.entity.id === selected)) {
      setSelected(rows[0]?.entity.id || '');
      setWaivers({});
    }
  }, [rows, selected, confirmation]);
  const blocked = busy || !!list.refreshing || !!list.error || !current?.can_decide;
  const confirmedRow = rows?.find((row) => row.entity.id === confirmation?.id);
  const confirmationBlocked = busy || !!list.refreshing || !!list.error || !confirmedRow?.can_decide;
  const failedChecks = gateBlockers(current?.gates, waivers);
  const approvalBlocked = blocked || failedChecks.length > 0;
  const confirmedGatesBlocked =
    confirmation?.decision === 'approve_publish' && gateBlockers(confirmedRow?.gates, waivers).length > 0;
  const decisionConflict =
    confirmation?.decision === 'return' &&
    (['QG1', 'QG3'] as const).some((code) => !!waivers[code]?.trim() && !!denials[code]?.trim());
  const sources = current?.source_refs || [];
  const conditionValue: unknown = current?.entity.payload.conditions;
  const conditions = (Array.isArray(conditionValue) ? conditionValue : [conditionValue])
    .map(businessText)
    .filter(Boolean);
  const reviewMeta = current?.entity.payload.r2_review;
  const sourceChangeset =
    reviewMeta &&
    typeof reviewMeta === 'object' &&
    'changeset_id' in reviewMeta &&
    typeof reviewMeta.changeset_id === 'string'
      ? '/build/changesets/' + encodeURIComponent(reviewMeta.changeset_id)
      : '/build/intake';
  const revisit = (step: number) =>
    navigate(step === 1 ? '/build/intake' : step === 2 ? sourceChangeset : '/govern/reviews');
  const blockedReason =
    current?.blocked_reason ||
    (!current?.can_decide
      ? copy.r3_main_review_other
      : failedChecks[0]?.reason || (failedChecks.length ? copy.r4_gate_waiver_required : ''));
  function confirm(decision: ReviewBody['decision']) {
    if (!current || blocked || (decision === 'approve_publish' && approvalBlocked)) return;
    setConfirmation({
      id: current.entity.id,
      version: current.entity.version,
      title: current.entity.title,
      decision,
    });
    setReason('');
    setDenials({});
    setError('');
  }
  async function decide() {
    const signal = lifetime.current?.signal;
    if (
      confirmationBlocked ||
      confirmedGatesBlocked ||
      decisionConflict ||
      !confirmation ||
      (confirmation.decision === 'return' && !reason.trim()) ||
      signal?.aborted
    )
      return;
    setBusy(true);
    setError('');
    const body: ReviewBody = {
      decision: confirmation.decision,
      version: confirmation.version,
      reason: reason.trim(),
      ...(confirmation.decision === 'return' && Object.values(denials).some((value) => value?.trim())
        ? {
            waiver_denials: Object.fromEntries(
              Object.entries(denials)
                .filter(([, value]) => value?.trim())
                .map(([code, value]) => [code, value?.trim()]),
            ),
          }
        : {}),
      ...(Object.values(waivers).some((value) => value?.trim())
        ? {
            waivers: Object.fromEntries(
              Object.entries(waivers)
                .filter(([, value]) => value?.trim())
                .map(([key, value]) => [key, value?.trim() || '']),
            ),
          }
        : {}),
    };
    try {
      await api<ReviewResult>('/v2/reviews/' + encodeURIComponent(confirmation.id) + ':decide', {
        method: 'POST',
        signal,
        body: JSON.stringify(body),
      });
      if (signal?.aborted) return;
      setResult({
        id: confirmation.id,
        title: confirmation.title,
        decision: confirmation.decision,
        reason: reason.trim(),
      });
      setReason('');
      setWaivers({});
      setSelected('');
      setConfirmation(undefined);
      list.reload();
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }
  return (
    <PageLayout
      variant="split"
      header={
        <PageHeader
          title={copy.r2_reviews_title}
          lead={
            rows?.length
              ? sentence(copy.r3_main_review_wait, { count: rows.length })
              : copy.r3_main_review_none
          }
          note={blockedReason || undefined}
          primaryAction={
            current
              ? {
                  label: actions.catalog_2_5,
                  disabled: approvalBlocked || !!confirmation,
                  onClick: () => confirm('approve_publish'),
                }
              : undefined
          }
        />
      }
    >
      {(error || list.error) && !confirmation && (
        <Alert
          type="error"
          title={error || list.error}
          role="alert"
          action={list.error && <Button onClick={list.reload}>{copy.admin_retry}</Button>}
        />
      )}
      {result && (
        <Alert
          type="success"
          role="status"
          title={result.decision === 'return' ? copy.r2_returned : copy.r2_reviewed}
          description={
            <>
              {result.title}
              {result.decision === 'return' && (
                <p>
                  {copy.r3_main_return_reason}：{result.reason}
                </p>
              )}
            </>
          }
          action={
            result.decision === 'approve_publish' && (
              <Button type="text" onClick={() => navigate(askHref(result.id))}>
                {copy.r3_main_ask}
              </Button>
            )
          }
        />
      )}
      <div className={styles.actions}>
        <Select
          aria-label={copy.r2_kind}
          value={kind}
          onChange={(value) => {
            setKind(value);
            setSelected('');
            setReason('');
          }}
          options={[
            { value: '', label: copy.r2_all_kinds },
            ...(['rule', 'data', 'knowledge', 'skill'] as const).map((value) => ({
              value,
              label: terms.kinds[value],
            })),
          ]}
        />
        <Button autoInsertSpace={false} onClick={list.reload}>
          {actions.r2_refresh}
        </Button>
        <span className={styles.muted}>{copy.r3_main_review_batch_reason}</span>
      </div>
      {!rows ? (
        <ResourceState error={list.error} reload={list.reload} />
      ) : !rows.length ? (
        <>
          <StateBlock
            state="empty"
            description={copy.r2_empty_reviews}
            action={{ label: copy.r2_catalog_title, onClick: () => navigate('/ontology/catalog') }}
          />
          <NextStepBar
            current={result?.decision === 'approve_publish' ? 4 : 3}
            onStep={revisit}
            complete={result?.decision === 'approve_publish'}
            description={
              result?.decision === 'approve_publish' ? copy.r3_main_next_ask : copy.r3_main_wait_review
            }
            action={
              result?.decision === 'approve_publish'
                ? { label: copy.r3_main_ask, onClick: () => navigate(askHref(result.id)) }
                : undefined
            }
          />
        </>
      ) : (
        <SplitView
          list={
            <div className={styles['list-pane']}>
              <div className={styles['pane-heading']}>
                <h2>{copy.r2_select_review}</h2>
                <span>{rows.length}</span>
              </div>
              <div className={styles['item-list']} aria-label={copy.r2_select_review}>
                {rows.map((row) => (
                  <button
                    type="button"
                    key={row.entity.id}
                    data-review-item-id={row.entity.id}
                    className={styles.pick}
                    aria-current={row.entity.id === current?.entity.id ? 'true' : undefined}
                    onClick={() => {
                      setSelected(row.entity.id);
                      setReason('');
                      setWaivers({});
                    }}
                  >
                    <strong>{row.entity.title}</strong>
                    <span>
                      {row.submitter} · {formatDateTime(row.submitted_at, true)}
                    </span>
                    <span className={styles['pick-state']}>
                      {terms.kinds[row.entity.kind as keyof typeof terms.kinds] || copy.unknown_kind}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          }
          footer={<NextStepBar current={3} onStep={revisit} description={copy.r3_main_next_ask} />}
        >
          {current && (
            <article className={styles.detail} data-review-id={current.entity.id}>
              <div className={styles['detail-body']}>
                <div className={styles.actions}>
                  <KindTag kind={current.entity.kind} />
                  <StatusTag status={current.entity.status} />
                  {(current.entity.payload.extraction_mode === 'online' ||
                    current.entity.payload.extraction_mode === 'local') && (
                    <OriginTag
                      origin={current.entity.payload.extraction_mode === 'online' ? 'ai' : 'local'}
                    />
                  )}
                </div>
                <h2>{current.entity.title}</h2>
                <p className={styles.statement}>
                  {businessText(current.entity.payload.content) ||
                    businessText(current.entity.payload.definition) ||
                    current.entity.summary}
                </p>
                <dl className={styles.metadata}>
                  <dt>{copy.r2_submitter}</dt>
                  <dd>{current.submitter}</dd>
                  <dt>{copy.r3_main_catalog_updated}</dt>
                  <dd>{formatDateTime(current.submitted_at)}</dd>
                </dl>
                <Link to={'/ontology/elements/' + encodeURIComponent(current.entity.id)}>
                  {copy.r4a1_legacy_details_link}
                </Link>
                {!!current.entity.payload.managed_skill && (
                  <Link to={'/skill-development/' + encodeURIComponent(current.entity.id)}>
                    {copy.r4a1_legacy_skill_link}
                  </Link>
                )}
                {!!current.gates?.fields?.length && (
                  <FoldRow
                    title={copy.r4a1_legacy_facts}
                    summary={sentence(copy.r3_main_count, { count: current.gates.fields.length })}
                  >
                    <GovernanceFacts gates={current.gates} showHeading={false} />
                  </FoldRow>
                )}
                {!current.gates?.fields?.length && conditions.length > 0 && (
                  <FoldRow
                    title={copy.r3_main_scope}
                    summary={sentence(copy.r3_main_count, { count: conditions.length })}
                    defaultOpen
                  >
                    <ul>
                      {conditions.map((condition, index) => (
                        <li key={index}>{condition}</li>
                      ))}
                    </ul>
                  </FoldRow>
                )}
                <h3>{copy.r3_main_source}</h3>
                {sources.length ? (
                  sources.map((source, index) => (
                    <blockquote className={styles.quote} key={index}>
                      <p>{sourceDescription(source) || copy.r3_main_no_source}</p>
                      {isAnchoredEvidence(source) && (
                        <Button type="text" autoInsertSpace={false} onClick={() => setFocus(source)}>
                          {copy.r3_main_view_original}
                        </Button>
                      )}
                    </blockquote>
                  ))
                ) : (
                  <p>{copy.r3_main_no_source}</p>
                )}
                <GateChecklist
                  key={current.entity.id}
                  gates={current.gates}
                  professional={professional}
                  waivers={waivers}
                  disabled={blocked || !!confirmation}
                  onWaiver={(code, value) => setWaivers((previous) => ({ ...previous, [code]: value }))}
                />
                <TechnicalDetails>
                  <p>{current.entity.id}</p>
                  <pre>
                    {JSON.stringify(
                      {
                        content: current.entity.payload.content,
                        definition: current.entity.payload.definition,
                        conditions: current.entity.payload.conditions,
                        source_refs: current.source_refs,
                      },
                      null,
                      2,
                    )}
                  </pre>
                </TechnicalDetails>
              </div>
              <div className={styles['pane-actions']}>
                <Button autoInsertSpace={false} disabled={blocked} onClick={() => confirm('return')}>
                  {actions.catalog_2_6}
                </Button>
              </div>
            </article>
          )}
        </SplitView>
      )}
      <SourceEvidence source={focus} onClose={() => setFocus(undefined)} />
      <Modal
        width="var(--zy-dialog-width)"
        styles={{ body: { maxHeight: 'calc(100dvh - var(--zy-space-10) * 4)', overflowY: 'auto' } }}
        cancelButtonProps={{ autoInsertSpace: false }}
        open={!!confirmation}
        title={confirmation?.decision === 'return' ? actions.catalog_2_6 : actions.catalog_2_5}
        okText={confirmation?.decision === 'return' ? actions.catalog_2_6 : actions.catalog_2_5}
        cancelText={actions.r2_cancel}
        onCancel={() => !busy && setConfirmation(undefined)}
        onOk={() => void decide()}
        confirmLoading={busy}
        okButtonProps={{
          autoInsertSpace: false,
          'aria-label': confirmation?.decision === 'return' ? actions.catalog_2_6 : actions.catalog_2_5,
          disabled:
            confirmationBlocked ||
            confirmedGatesBlocked ||
            decisionConflict ||
            (confirmation?.decision === 'return' && !reason.trim()),
        }}
      >
        {error && <Alert type="error" title={error} role="alert" />}
        {confirmation && !list.refreshing && !confirmedRow?.can_decide && (
          <Alert type="warning" title={confirmedRow?.blocked_reason || copy.r31_review_changed} />
        )}
        <p data-testid="review-confirmation-object" data-review-id={confirmation?.id}>
          {confirmation?.title}
        </p>
        <p>
          {confirmation?.decision === 'return' ? copy.r3_main_return_impact : copy.r3_main_approve_impact}
        </p>
        {confirmation?.decision === 'approve_publish' && (
          <>
            <GovernanceFacts gates={confirmedRow?.gates} />
            <GateChecklist gates={confirmedRow?.gates} professional waivers={waivers} />
          </>
        )}
        {confirmation?.decision === 'return' && (
          <>
            {decisionConflict && <Alert type="warning" title={copy.r4a1_legacy_decision_conflict} />}
            {(confirmedRow?.gates?.checks || [])
              .filter((check) => check.application_reason && (check.code === 'QG1' || check.code === 'QG3'))
              .map((check) => (
                <label className={styles.field} key={check.code}>
                  {check.label} · {copy.r4a1_legacy_denial}
                  {waivers[check.code as 'QG1' | 'QG3']?.trim() && (
                    <p>
                      {copy.r4_gate_waiver}：{waivers[check.code as 'QG1' | 'QG3']}
                    </p>
                  )}
                  <p>
                    {copy.r4a1_legacy_application}：{check.application_reason}
                  </p>
                  <Input.TextArea
                    aria-label={`${check.label} · ${copy.r4a1_legacy_denial}`}
                    rows={2}
                    maxLength={500}
                    value={denials[check.code as 'QG1' | 'QG3'] || ''}
                    placeholder={copy.r4a1_legacy_denial_hint}
                    onChange={(event) =>
                      setDenials((previous) => ({ ...previous, [check.code]: event.target.value }))
                    }
                  />
                </label>
              ))}
            <label className={styles.field}>
              {copy.r3_main_return_reason}
              <Input.TextArea
                autoFocus
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={4}
                maxLength={2000}
                placeholder={copy.r3_main_return_hint}
              />
            </label>
          </>
        )}
      </Modal>
    </PageLayout>
  );
}

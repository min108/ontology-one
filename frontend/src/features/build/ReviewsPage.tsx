import { Alert, Button, Input, Modal, Select } from 'antd';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
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
    [confirmation, setConfirmation] = useState<ReviewBody['decision']>();
  const [result, setResult] = useState<{
    id: string;
    title: string;
    decision: ReviewBody['decision'];
    reason: string;
  }>();
  const [focus, setFocus] = useState<Evidence>();
  const lifetime = useLifetime(),
    navigate = useNavigate();
  const rows = list.data?.data.items;
  const current = rows?.find((row) => row.entity.id === selected) || rows?.[0];
  const blocked = busy || !!list.refreshing || !!list.error || !current?.can_decide;
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
  const blockedReason = current?.blocked_reason || (!current?.can_decide ? copy.r3_main_review_other : '');
  async function decide() {
    const signal = lifetime.current?.signal;
    if (
      !current?.can_decide ||
      blocked ||
      !confirmation ||
      (confirmation === 'return' && !reason.trim()) ||
      signal?.aborted
    )
      return;
    setBusy(true);
    setError('');
    const body: ReviewBody = {
      decision: confirmation,
      version: current.entity.version,
      reason: reason.trim(),
    };
    try {
      await api<ReviewResult>('/v2/reviews/' + encodeURIComponent(current.entity.id) + ':decide', {
        method: 'POST',
        signal,
        body: JSON.stringify(body),
      });
      if (signal?.aborted) return;
      setResult({
        id: current.entity.id,
        title: current.entity.title,
        decision: confirmation,
        reason: reason.trim(),
      });
      setReason('');
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
                  disabled: blocked || !!confirmation,
                  onClick: () => {
                    setConfirmation('approve_publish');
                    setError('');
                  },
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
                    className={styles.pick}
                    aria-current={row.entity.id === current?.entity.id ? 'true' : undefined}
                    onClick={() => {
                      setSelected(row.entity.id);
                      setReason('');
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
                {blockedReason && (
                  <Alert
                    type="warning"
                    title={
                      /提交人|最后编辑人|自己的/.test(blockedReason)
                        ? copy.r3_main_review_self
                        : copy.r3_main_review_other
                    }
                    description={blockedReason}
                  />
                )}
                {conditions.length > 0 && (
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
                {professional && (
                  <FoldRow
                    title={copy.r3_main_five_checks}
                    summary={copy.r3_main_pro_checks_summary}
                    defaultOpen
                  >
                    <ul className={styles.checks}>
                      <li>
                        {copy.r3_main_source_check} ·{' '}
                        {sources.some(isAnchoredEvidence)
                          ? copy.r3_main_check_pass
                          : copy.r3_main_check_pending}
                      </li>
                      <li>
                        {copy.r3_main_content_check} · {copy.r3_main_check_unknown}
                      </li>
                      <li>
                        {copy.r3_main_duplicate_check} · {copy.r3_main_check_unknown}
                      </li>
                      <li>
                        {copy.r3_main_check_scope} · {copy.r3_main_check_unknown}
                      </li>
                      <li>
                        {copy.r3_main_check_review} ·{' '}
                        {current.can_decide ? copy.r3_main_check_pass : blockedReason}
                      </li>
                    </ul>
                  </FoldRow>
                )}
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
                <Button
                  autoInsertSpace={false}
                  disabled={blocked}
                  onClick={() => {
                    setConfirmation('return');
                    setError('');
                  }}
                >
                  {actions.catalog_2_6}
                </Button>
                {!current.can_decide && <span className={styles.muted}>{copy.r3_main_review_other}</span>}
              </div>
            </article>
          )}
        </SplitView>
      )}
      <SourceEvidence source={focus} onClose={() => setFocus(undefined)} />
      <Modal
        open={!!confirmation}
        title={confirmation === 'return' ? actions.catalog_2_6 : actions.catalog_2_5}
        okText={confirmation === 'return' ? actions.catalog_2_6 : actions.catalog_2_5}
        cancelText={actions.r2_cancel}
        onCancel={() => !busy && setConfirmation(undefined)}
        onOk={() => void decide()}
        confirmLoading={busy}
        okButtonProps={{
          autoInsertSpace: false,
          'aria-label': confirmation === 'return' ? actions.catalog_2_6 : actions.catalog_2_5,
          disabled: blocked || (confirmation === 'return' && !reason.trim()),
        }}
      >
        {error && <Alert type="error" title={error} role="alert" />}
        <p>{current?.entity.title}</p>
        <p>{confirmation === 'return' ? copy.r3_main_return_impact : copy.r3_main_approve_impact}</p>
        {confirmation === 'return' && (
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
        )}
      </Modal>
    </PageLayout>
  );
}

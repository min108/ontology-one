import { Alert, Button, Input, Modal, Select } from 'antd';
import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { invokeCapability } from '../../api/client';
import type { CandidatePatch, ChangesetGet, DecideItems, ProposalResult } from '../../api/generated/types';
import {
  PageHeader,
  PageLayout,
  SplitView,
  NextStepBar,
  EvidenceDrawer,
  SourceViewer,
  OriginTag,
  FoldRow,
  TechnicalDetails,
  KindTag,
  StatusTag,
} from '../../ui';
import { useUiStore } from '../../state/ui';
import { terms } from '../../i18n/zh-CN';
import { ResourceState, useResource } from '../admin/shared';
import {
  actions,
  copy,
  type Evidence,
  type Item,
  sourceHref,
  useBuildIdentity,
  useLifetime,
  BuildForbidden,
} from './shared';
import { changesetGate, groupItems, isHandledElsewhere, sentence } from './model';
import styles from './Build.module.css';

export default function ChangesetPage() {
  const { id = '' } = useParams();
  const identity = useBuildIdentity();
  return identity.canRead ? (
    <Changeset key={identity.key + id} id={id} canEdit={identity.canEdit} />
  ) : (
    <BuildForbidden />
  );
}
function Changeset({ id, canEdit }: { id: string; canEdit: boolean }) {
  const resource = useResource<ProposalResult>('/v2/changesets/' + encodeURIComponent(id));
  const professional = useUiStore((state) => state.viewMode === 'professional');
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('');
  const [selected, setSelected] = useState(''),
    [focus, setFocus] = useState<Evidence>();
  const [editing, setEditing] = useState<Item>(),
    [confirm, setConfirm] = useState(false);
  const [undo, setUndo] = useState<Item[]>();
  const [draft, setDraft] = useState<CandidatePatch>({ title: '', content: '', kind: 'rule' });
  const lifetime = useLifetime();
  const proposal = resource.data?.data;
  useEffect(() => {
    if (!undo) return;
    const timer = setTimeout(() => setUndo(undefined), 8000);
    return () => clearTimeout(timer);
  }, [undo]);
  async function perform(operation: () => Promise<unknown>, success: string) {
    if (!canEdit || busy || resource.refreshing || resource.error || lifetime.current?.signal.aborted) return;
    const signal = lifetime.current?.signal;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await operation();
      if (signal?.aborted) return;
      setMessage(success);
      setEditing(undefined);
      setConfirm(false);
    } catch (failure) {
      if (!signal?.aborted) setError(failure instanceof Error ? failure.message : copy.r2_failure);
    } finally {
      if (!signal?.aborted) {
        resource.reload();
        setBusy(false);
      }
    }
  }
  function decide(items: Item[], decision: DecideItems['decision'], edit?: CandidatePatch) {
    return perform(
      async () => {
        const body: DecideItems = {
          changeset_id: id,
          item_ids: items.map((item) => item.id),
          decision,
          ...(edit ? { edit } : {}),
        };
        const options = { signal: lifetime.current?.signal };
        await invokeCapability('changeset.decide_items', body, options);
        if (['accepted', 'edited'].includes(decision))
          await invokeCapability('changeset.apply', { changeset_id: id } satisfies ChangesetGet, options);
        setUndo(decision === 'rejected' ? items : undefined);
      },
      decision === 'rejected'
        ? copy.r3_main_undo_saved
        : decision === 'pending'
          ? copy.r2_saved
          : copy.r2_drafts,
    );
  }
  if (!proposal)
    return (
      <PageLayout variant="split" header={<PageHeader title={copy.r2_changeset_title} />}>
        <ResourceState error={resource.error} reload={resource.reload} />
      </PageLayout>
    );
  const gate = changesetGate(proposal, canEdit);
  const groups = groupItems(proposal.items);
  const current =
    proposal.items.find((item) => item.id === selected) || groups.flatMap((group) => group.items)[0];
  const submitted = !proposal.can_submit && gate.pending === 0 && gate.accepted > 0;
  const blocked = !canEdit || busy || resource.refreshing || !!resource.error;
  const locked = blocked || !current || isHandledElsewhere(current) || !!current.applied_entity_id;
  const sure = proposal.items.filter(
    (item) =>
      item.tier === 'sure' &&
      item.decision === 'pending' &&
      !isHandledElsewhere(item) &&
      item.validation.level !== 'error',
  );
  const chosen = current?.evidence[0];
  const note = !canEdit
    ? copy.r3_main_read_only
    : submitted
      ? copy.r3_main_submitted
      : gate.pending
        ? sentence(copy.r3_main_pending, { count: gate.pending })
        : !gate.accepted
          ? copy.r3_main_no_adopted
          : undefined;
  const lead = gate.pending
    ? proposal.title
    : sentence(copy.r3_main_done, {
        count: proposal.items.length,
        accepted: gate.accepted,
        rejected: gate.rejected,
      });
  return (
    <PageLayout
      variant="split"
      header={
        <PageHeader
          title={copy.r2_changeset_title}
          back={{ label: copy.r2_intake_title, href: '/build/intake' }}
          lead={lead}
          note={note}
          status={<StatusTag domain="proposal" status={proposal.status} />}
          primaryAction={{
            label: submitted ? copy.r3_main_submitted : actions.catalog_1_6,
            disabled: blocked || !gate.allowed || confirm || !!editing,
            onClick: () => setConfirm(true),
          }}
        />
      }
    >
      {(error || resource.error) && !editing && !confirm && (
        <Alert
          type="error"
          title={error || resource.error}
          role="alert"
          action={resource.error && <Button onClick={resource.reload}>{copy.admin_retry}</Button>}
        />
      )}
      {message && (
        <Alert
          type="success"
          title={message}
          role="status"
          action={
            undo && (
              <Button type="text" disabled={blocked} onClick={() => void decide(undo, 'pending')}>
                {copy.r3_main_undo}
              </Button>
            )
          }
        />
      )}
      <SplitView
        list={
          <div className={styles['list-pane']}>
            <div className={styles['pane-heading']}>
              <h2>{copy.r3_main_items}</h2>
              <span className={styles.muted}>
                {sentence(copy.r3_main_handled, {
                  done: proposal.items.length - gate.pending,
                  count: proposal.items.length,
                })}
              </span>
            </div>
            <div className={styles['item-list']}>
              {groups
                .filter((group) => group.items.length)
                .map((group) => (
                  <section key={group.tier} aria-label={terms.statuses.confidence[group.tier]}>
                    <h3 className={styles.group}>
                      {terms.statuses.confidence[group.tier]} · {group.items.length}
                    </h3>
                    {group.items.map((item) => (
                      <button
                        type="button"
                        className={styles.pick}
                        data-item-id={item.id}
                        aria-current={item.id === current?.id ? 'true' : undefined}
                        aria-label={item.after_data.title}
                        key={item.id}
                        onClick={() => setSelected(item.id)}
                      >
                        <strong>{item.after_data.title}</strong>
                        <span>
                          {terms.kinds[item.after_data.kind as keyof typeof terms.kinds] || copy.unknown_kind}
                        </span>
                        <span className={styles['pick-state']}>
                          {isHandledElsewhere(item)
                            ? copy.r3_main_elsewhere
                            : terms.statuses.decision[
                                item.decision as keyof typeof terms.statuses.decision
                              ] || copy.unknown_status}
                        </span>
                      </button>
                    ))}
                  </section>
                ))}
            </div>
            {sure.length > 0 && (
              <div className={styles['pane-actions']}>
                <Button
                  autoInsertSpace={false}
                  disabled={blocked}
                  onClick={() => void decide(sure, 'accepted')}
                >
                  {actions.catalog_1_4}
                </Button>
              </div>
            )}
          </div>
        }
        aside={
          <div className={`${styles.stack} ${styles['evidence-pane']}`}>
            <h2>{copy.r3_main_source}</h2>
            <p>{proposal.title}</p>
            {chosen && (
              <>
                <p>{chosen.locator}</p>
              </>
            )}
            <h2>{copy.r3_main_checks}</h2>
            <ul className={styles.checks}>
              <li>
                {copy.r3_main_source_check} · {chosen ? copy.r3_main_check_pass : copy.r3_main_check_pending}
              </li>
              <li>
                {copy.r3_main_content_check} ·{' '}
                {current?.validation.level === 'error' ? copy.r3_main_check_pending : copy.r3_main_check_pass}
              </li>
              <li>
                {copy.r3_main_duplicate_check} ·{' '}
                {current?.duplicate_of ? copy.r2_duplicate : copy.r3_main_no_duplicate}
              </li>
            </ul>
            {professional && current && (
              <FoldRow title={copy.r3_main_confidence} summary={terms.statuses.confidence[current.tier]}>
                <p>{current.rationale || copy.r3_main_check_unknown}</p>
                {(current.validation.issues || []).map((issue) => (
                  <p key={issue}>{issue}</p>
                ))}
              </FoldRow>
            )}
          </div>
        }
        footer={
          <NextStepBar
            current={submitted ? 3 : 2}
            description={submitted ? copy.r3_main_wait_review : copy.r3_main_next_submit}
            action={
              submitted
                ? { label: copy.r2_review_link, onClick: () => navigate('/govern/reviews') }
                : undefined
            }
          />
        }
      >
        {current ? (
          <div className={styles.detail} data-selected-item-id={current.id}>
            <div className={styles['detail-body']}>
              {!gate.pending && (
                <div className={styles.completed}>
                  <h2>{copy.r3_main_submit_content}</h2>
                  <p>{sentence(copy.r3_main_submit_count, { count: gate.accepted })}</p>
                  <h3>{copy.r3_main_after_submit}</h3>
                  <p>{copy.r3_main_submit_impact}</p>
                </div>
              )}
              <div className={styles.actions}>
                <KindTag kind={current.after_data.kind} />
                <StatusTag
                  domain="decision"
                  status={current.decision === 'processed_elsewhere' ? 'rejected' : current.decision}
                />
              </div>
              <h2>{current.after_data.title}</h2>
              <p className={styles.statement}>{current.one_liner}</p>
              <OriginTag
                origin={
                  current.content_origin === 'local'
                    ? 'local'
                    : current.content_origin === 'mock'
                      ? 'mock'
                      : 'ai'
                }
              />
              {isHandledElsewhere(current) ? (
                <p>{copy.r3_main_elsewhere}</p>
              ) : (
                current.tier !== 'sure' && (
                  <Alert
                    type="warning"
                    title={copy.r3_main_needs_confirm}
                    description={
                      current.rationale || current.validation.issues?.join('；') || copy.r3_main_review_source
                    }
                  />
                )
              )}
              <h3>{copy.r2_original}</h3>
              {chosen && (
                <Button type="text" autoInsertSpace={false} onClick={() => setFocus(chosen)}>
                  {copy.r3_main_view_original}
                </Button>
              )}
              {chosen ? (
                <SourceViewer
                  text={chosen.quote}
                  quote={chosen.quote}
                  start={0}
                  end={Array.from(chosen.quote).length}
                />
              ) : (
                <p>{copy.r3_main_no_source}</p>
              )}
              {professional && (
                <FoldRow title={copy.r3_main_structure} summary={copy.r2_content}>
                  <p className={styles.paragraph}>{current.after_data.content}</p>
                </FoldRow>
              )}
              <TechnicalDetails>
                <p>{current.id}</p>
                <p>{chosen?.source_version}</p>
                {chosen && (
                  <p>
                    {chosen.start}–{chosen.end}
                  </p>
                )}
              </TechnicalDetails>
            </div>
            <div className={styles['pane-actions']}>
              {current.applied_entity_id ? (
                <Link to={'/ontology/elements/' + encodeURIComponent(current.applied_entity_id)}>
                  {copy.r2_view_draft}
                </Link>
              ) : (
                <>
                  <Button
                    autoInsertSpace={false}
                    disabled={locked || current.validation.level === 'error'}
                    onClick={() => void decide([current], 'accepted')}
                  >
                    {actions.catalog_1_0}
                  </Button>
                  <Button
                    autoInsertSpace={false}
                    disabled={locked}
                    onClick={() => {
                      setEditing(current);
                      setError('');
                      setDraft({
                        title: current.after_data.title,
                        content: current.after_data.content || '',
                        kind: current.after_data.kind as CandidatePatch['kind'],
                      });
                    }}
                  >
                    {actions.catalog_1_1}
                  </Button>
                  <Button
                    autoInsertSpace={false}
                    type="text"
                    disabled={locked || current.decision === 'rejected'}
                    onClick={() => void decide([current], 'rejected')}
                  >
                    {actions.catalog_1_2}
                  </Button>
                </>
              )}
            </div>
          </div>
        ) : (
          <p>{copy.r2_empty_items}</p>
        )}
      </SplitView>
      <EvidenceDrawer
        open={!!focus}
        onClose={() => setFocus(undefined)}
        title={copy.r2_original}
        footer={focus && <Link to={sourceHref(focus)}>{copy.r3_main_source_page}</Link>}
      >
        <SourceViewer
          text={proposal.source_text}
          quote={focus?.quote || ''}
          start={focus?.start}
          end={focus?.end}
        />
        <TechnicalDetails>
          {focus?.source_version}
          <p>
            {focus?.start}–{focus?.end}
          </p>
        </TechnicalDetails>
      </EvidenceDrawer>
      <Modal
        title={actions.catalog_1_6}
        open={confirm}
        onCancel={() => !busy && setConfirm(false)}
        okText={actions.catalog_1_6}
        cancelText={actions.r2_cancel}
        confirmLoading={busy}
        okButtonProps={{ disabled: blocked || !gate.allowed }}
        onOk={() =>
          void perform(
            () =>
              invokeCapability('changeset.apply_and_submit', { changeset_id: id } satisfies ChangesetGet, {
                signal: lifetime.current?.signal,
              }),
            copy.r2_submitted,
          )
        }
      >
        {error && <Alert type="error" title={error} role="alert" />}
        <p>{sentence(copy.r3_main_submit_count, { count: gate.accepted })}</p>
        <p>{copy.r3_main_submit_checks}</p>
        <p>{copy.r3_main_submit_impact}</p>
      </Modal>
      <Modal
        title={actions.catalog_1_1}
        open={!!editing}
        onCancel={() => !busy && setEditing(undefined)}
        okText={actions.r2_save}
        cancelText={actions.r2_cancel}
        confirmLoading={busy}
        okButtonProps={{
          disabled: !!resource.refreshing || !!resource.error || !draft.title.trim() || !draft.content.trim(),
          'aria-label': actions.r2_save,
        }}
        onOk={() => editing && void decide([editing], 'edited', draft)}
      >
        {(error || resource.error) && (
          <Alert
            type="error"
            title={error || resource.error}
            role="alert"
            action={resource.error && <Button onClick={resource.reload}>{copy.admin_retry}</Button>}
          />
        )}
        <label className={styles.field}>
          {copy.r2_title}
          <Input
            value={draft.title}
            maxLength={250}
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
          />
        </label>
        <label className={styles.field}>
          {copy.r2_kind}
          <Select
            aria-label={copy.r2_kind}
            value={draft.kind}
            onChange={(kind) => setDraft({ ...draft, kind })}
            options={(['rule', 'data', 'knowledge', 'skill'] as const).map((kind) => ({
              value: kind,
              label: terms.kinds[kind],
            }))}
          />
        </label>
        <label className={styles.field}>
          {copy.r2_content}
          <Input.TextArea
            rows={7}
            value={draft.content}
            maxLength={16000}
            onChange={(event) => setDraft({ ...draft, content: event.target.value })}
          />
        </label>
      </Modal>
    </PageLayout>
  );
}

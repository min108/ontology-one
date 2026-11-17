import { Alert, Button, DatePicker, Input, Modal, Select } from 'antd';
import dayjs from 'dayjs';
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
import { sourceLocator } from './businessText';
import { GateChecklist } from './GateChecklist';
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
  const [judgementText, setJudgementText] = useState('');
  const [judgementError, setJudgementError] = useState('');
  const [undo, setUndo] = useState<Item[]>();
  const [draft, setDraft] = useState<CandidatePatch>({ title: '', content: '', kind: 'rule' });
  const lifetime = useLifetime();
  const proposal = resource.data?.data;
  useEffect(() => {
    if (!undo) return;
    const timer = setTimeout(() => {
      setUndo(undefined);
      setMessage((value) => (value === copy.r3_main_undo_saved ? '' : value));
    }, 8000);
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
  const intakeHref =
    typeof proposal.origin_ref === 'string'
      ? '/build/intake?' + new URLSearchParams({ run: proposal.origin_ref })
      : '/build/intake';
  const gate = changesetGate(proposal, canEdit);
  const groups = groupItems(proposal.items);
  const current =
    proposal.items.find((item) => item.id === selected) || groups.flatMap((group) => group.items)[0];
  const submitBlockers = proposal.submit_blockers || [];
  const submitted = !proposal.can_submit && !submitBlockers.length && gate.pending === 0 && gate.accepted > 0;
  const blocked = !canEdit || busy || resource.refreshing || !!resource.error;
  const locked = blocked || !current || isHandledElsewhere(current) || submitted;
  const sure = proposal.items.filter(
    (item) =>
      item.tier === 'sure' &&
      item.decision === 'pending' &&
      !isHandledElsewhere(item) &&
      item.validation.level !== 'error',
  );
  const chosen = current?.evidence[0];
  const note =
    submitBlockers[0] ||
    (!canEdit
      ? copy.r3_main_read_only
      : submitted
        ? copy.r3_main_submitted
        : gate.pending
          ? sentence(copy.r3_main_pending, { count: gate.pending })
          : !gate.accepted
            ? copy.r3_main_no_adopted
            : undefined);
  const lead = gate.pending
    ? proposal.title
    : sentence(copy.r3_main_done, {
        count: proposal.items.length,
        a: gate.accepted,
        r: gate.rejected,
      });
  return (
    <PageLayout
      variant="split"
      header={
        <PageHeader
          title={copy.r2_changeset_title}
          back={{ label: copy.r2_intake_title, href: intakeHref }}
          lead={lead}
          note={note}
          status={<StatusTag domain="proposal" status={proposal.status} />}
          primaryAction={{
            label: submitted ? copy.r3_main_submitted : actions.catalog_1_6,
            disabled: blocked || !gate.allowed || !!submitBlockers.length || confirm || !!editing,
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
                        {item.gates && !item.gates.passed && (
                          <span>{item.gates.blockers?.[0] || copy.r4_gates_fail}</span>
                        )}
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
            {chosen && sourceLocator(chosen.locator) && <p>{sourceLocator(chosen.locator)}</p>}
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
            onStep={(step) =>
              navigate(step === 1 ? intakeHref : '/build/changesets/' + encodeURIComponent(id))
            }
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
                  <p>
                    {sentence(submitted ? copy.r31_submitted_count : copy.r3_main_submit_count, {
                      count: gate.accepted,
                    })}
                  </p>
                  <h3>{copy.r3_main_after_submit}</h3>
                  <p>{copy.r3_main_submit_impact}</p>
                </div>
              )}
              <div className={styles.actions}>
                <KindTag kind={current.after_data.kind} />
                <StatusTag
                  domain="decision"
                  status={isHandledElsewhere(current) ? 'processed_elsewhere' : current.decision}
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
              <GateChecklist key={current.id} gates={current.gates} professional={professional} />
              {current.after_data.applies_to_title && (
                <p>
                  {copy.r4_rule_applies_to}：{current.after_data.applies_to_title}。
                  {copy.r4_rule_relation_accept}
                </p>
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
                <p>{chosen?.locator}</p>
                <p>{chosen?.source_version}</p>
                {chosen && (
                  <p>
                    {chosen.start}–{chosen.end}
                  </p>
                )}
              </TechnicalDetails>
            </div>
            <div className={styles['pane-actions']}>
              <Button
                autoInsertSpace={false}
                disabled={locked}
                onClick={() => {
                  setEditing(current);
                  setError('');
                  setJudgementError('');
                  setJudgementText(
                    current.after_data.judgement ? JSON.stringify(current.after_data.judgement, null, 2) : '',
                  );
                  setDraft({
                    title: current.after_data.title,
                    content: current.after_data.content || '',
                    kind: current.after_data.kind as CandidatePatch['kind'],
                    ...Object.fromEntries(
                      (
                        [
                          'definition',
                          'scope',
                          'conditions',
                          'exceptions',
                          'valid_from',
                          'judgement',
                          'unit',
                          'grain',
                          'boundary',
                        ] as const
                      )
                        .filter((key) => current.after_data[key] !== undefined)
                        .map((key) => [key, current.after_data[key]]),
                    ),
                  });
                }}
              >
                {actions.catalog_1_1}
              </Button>
              {current.applied_entity_id ? (
                <Link
                  to={
                    '/ontology/elements/' +
                    encodeURIComponent(current.applied_entity_id) +
                    '?' +
                    new URLSearchParams({ from: '/build/changesets/' + encodeURIComponent(id) })
                  }
                >
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
        width="var(--zy-dialog-width)"
        title={actions.catalog_1_6}
        open={confirm}
        onCancel={() => !busy && setConfirm(false)}
        okText={actions.catalog_1_6}
        cancelText={actions.r2_cancel}
        confirmLoading={busy}
        okButtonProps={{ disabled: blocked || !gate.allowed || !!submitBlockers.length }}
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
        onOk={() => {
          if (!editing) return;
          try {
            const judgement = judgementText.trim() ? JSON.parse(judgementText) : null;
            if (
              judgementText.trim() &&
              (!judgement || typeof judgement !== 'object' || Array.isArray(judgement))
            )
              throw new Error();
            void decide(
              [editing],
              'edited',
              judgementText.trim() || editing.after_data.judgement ? { ...draft, judgement } : draft,
            );
          } catch {
            setJudgementError(copy.r4_rule_json_error);
          }
        }}
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
        <label className={styles.field}>
          {draft.kind === 'rule' ? copy.r4_rule_definition : copy.r2_card_field_definition}
          <Input.TextArea
            aria-label={draft.kind === 'rule' ? copy.r4_rule_definition : copy.r2_card_field_definition}
            value={draft.definition || ''}
            rows={2}
            maxLength={4000}
            onChange={(event) => setDraft({ ...draft, definition: event.target.value })}
          />
        </label>
        {draft.kind === 'data' &&
          (['unit', 'grain'] as const).map((field) => (
            <label className={styles.field} key={field}>
              {field === 'unit' ? copy.r2_card_field_unit : copy.r4_rule_grain}
              <Input
                aria-label={field === 'unit' ? copy.r2_card_field_unit : copy.r4_rule_grain}
                value={draft[field] || ''}
                onChange={(event) => setDraft({ ...draft, [field]: event.target.value })}
              />
            </label>
          ))}
        {draft.kind === 'knowledge' && (
          <label className={styles.field}>
            {copy.r2_card_field_boundary}
            <Input.TextArea
              aria-label={copy.r2_card_field_boundary}
              value={draft.boundary || ''}
              rows={2}
              onChange={(event) => setDraft({ ...draft, boundary: event.target.value })}
            />
          </label>
        )}
        {['rule', 'knowledge'].includes(draft.kind) && (
          <>
            {(draft.kind === 'rule' ? (['scope', 'exceptions'] as const) : []).map((field) => (
              <label className={styles.field} key={field}>
                {copy[`r4_rule_${field}`]}
                <Input.TextArea
                  aria-label={copy[`r4_rule_${field}`]}
                  value={draft[field] || ''}
                  rows={2}
                  maxLength={4000}
                  onChange={(event) => setDraft({ ...draft, [field]: event.target.value })}
                />
              </label>
            ))}
            <label className={styles.field}>
              {copy.r4_rule_conditions}
              <Input.TextArea
                aria-label={copy.r4_rule_conditions}
                value={(draft.conditions || []).join('\n')}
                rows={3}
                placeholder={copy.r4_rule_conditions_hint}
                onChange={(event) => setDraft({ ...draft, conditions: event.target.value.split('\n') })}
              />
            </label>
            {draft.kind === 'rule' && (
              <label className={styles.field}>
                {copy.r4_rule_valid_from}
                <DatePicker
                  aria-label={copy.r4_rule_valid_from}
                  value={draft.valid_from ? dayjs(draft.valid_from) : null}
                  placeholder={copy.r4_rule_date_hint}
                  onChange={(value) => setDraft({ ...draft, valid_from: value?.format('YYYY-MM-DD') || '' })}
                />
              </label>
            )}
          </>
        )}
        {professional && draft.kind === 'rule' && (
          <TechnicalDetails>
            {judgementError && <Alert type="error" title={judgementError} />}
            <label className={styles.field}>
              {copy.r4_rule_judgement}
              <Input.TextArea
                rows={10}
                aria-label={copy.r4_rule_judgement}
                value={judgementText}
                maxLength={64000}
                onChange={(event) => {
                  setJudgementText(event.target.value);
                  setJudgementError('');
                }}
              />
            </label>
          </TechnicalDetails>
        )}
      </Modal>
    </PageLayout>
  );
}

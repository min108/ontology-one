import { Alert, Button, Modal } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore } from '../../state/session';
import { useScopeStore } from '../../state/scope';
import { isWorkspaceConfirmationOpen, isWorkspaceLeaving } from '../../state/workspace-guard';
import { useWorkspaceGuard } from '../../app/useWorkspaceGuard';
import { DropOverlay } from '../../ui/DropOverlay';
import { requestRouteNavigation } from '../../app/guardedNavigation';
import { terms } from '../../i18n/zh-CN';
import {
  classifyFiles,
  fileProblems,
  stageFiles,
  pendingTransfers,
  acknowledgeTransfer,
  type IntakeTarget,
} from './intake';
import styles from './Home.module.css';
export function HomeIntake() {
  const session = useSessionStore((state) => state.session);
  const { tenantId, scopeKey } = useScopeStore();
  if (!session || !tenantId || tenantId !== session.workspace.tenant_id) return null;
  return <WorkspaceIntake key={JSON.stringify([session.user.id, tenantId, scopeKey])} />;
}
function WorkspaceIntake() {
  const session = useSessionStore((state) => state.session),
    scopeKey = useScopeStore((state) => state.scopeKey);
  const navigate = useNavigate();
  const [files, setFiles] = useState<File[]>([]),
    [open, setOpen] = useState(false),
    [error, setError] = useState(''),
    [generation, setGeneration] = useState(0),
    [transferRevision, setTransferRevision] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useWorkspaceGuard(files.length > 0);
  const allowed = !!session?.permissions.can_edit && !session.workspace.readonly;
  const take = (incoming: File[]) => {
    if (isWorkspaceLeaving() || isWorkspaceConfirmationOpen()) return;
    if (!allowed) {
      setError(terms.labels.home_readonly_files);
      return;
    }
    setError('');
    const all = [...files, ...incoming];
    const target = classifyFiles(all);
    if ((target === 'materials' || target === 'datasets') && !fileProblems(all, target).length) {
      handoff(all, target, []);
    } else {
      setFiles(all);
      setOpen(true);
    }
  };
  function handoff(selected: File[], target: IntakeTarget, remaining: File[]) {
    if (!session || !allowed) return;
    const problems = fileProblems(selected, target);
    if (problems.length) {
      setError(problems.join(' '));
      return;
    }
    try {
      const id = stageFiles(
        { userId: session.user.id, tenantId: session.workspace.tenant_id, scopeKey },
        target,
        selected,
      );
      setFiles(remaining);
      setOpen(false);
      setError('');
      requestRouteNavigation('/' + target + '?' + new URLSearchParams({ upload: '1', intake: id }), navigate);
    } catch {
      setError(terms.labels.home_transfer_unavailable);
      setFiles([...selected, ...remaining]);
      setOpen(true);
    }
  }
  function send(target: IntakeTarget) {
    const selected = files.filter((file) => !fileProblems([file], target).length);
    if (!selected.length) {
      setError(target === 'materials' ? terms.labels.home_material_limit : terms.labels.home_dataset_limit);
      return;
    }
    handoff(
      selected,
      target,
      files.filter((file) => !selected.includes(file)),
    );
  }
  useEffect(() => {
    const choose = () => {
      if (!isWorkspaceLeaving() && !isWorkspaceConfirmationOpen()) input.current?.click();
    };
    window.addEventListener('ontology:choose-files', choose);
    return () => window.removeEventListener('ontology:choose-files', choose);
  }, []);
  useEffect(() => {
    const clear = () => {
      setGeneration((value) => value + 1);
      setFiles([]);
      setOpen(false);
      setError('');
    };
    window.addEventListener('ontology:scope-cleared', clear);
    return () => window.removeEventListener('ontology:scope-cleared', clear);
  }, []);
  useEffect(() => {
    const changed = () => setTransferRevision((value) => value + 1);
    window.addEventListener('ontology:home-transfers', changed);
    return () => window.removeEventListener('ontology:home-transfers', changed);
  }, []);
  void transferRevision;
  const currentScope = session
    ? { userId: session.user.id, tenantId: session.workspace.tenant_id, scopeKey }
    : null;
  const pending = currentScope ? pendingTransfers(currentScope) : [];
  const classification = classifyFiles(files);
  return (
    <>
      <input
        ref={input}
        hidden
        aria-label={terms.labels.home_choose_files}
        type="file"
        multiple
        accept=".txt,.md,.csv,.pdf,.docx,.html,.htm,.xlsx"
        onChange={(event) => {
          take(Array.from(event.target.files ?? []));
          event.target.value = '';
        }}
      />
      <DropOverlay onFiles={take} />
      {pending.map((transfer) => (
        <Alert
          key={transfer.id}
          type="info"
          title={terms.labels.home_intake_pending}
          description={
            <ul>
              {transfer.files.map((file, index) => (
                <li key={index}>{file.name}</li>
              ))}
            </ul>
          }
          action={
            <div className={styles.actions}>
              <Button
                disabled={!allowed}
                onClick={() => {
                  if (!isWorkspaceLeaving() && !isWorkspaceConfirmationOpen())
                    requestRouteNavigation(
                      '/' + transfer.target + '?' + new URLSearchParams({ upload: '1', intake: transfer.id }),
                      navigate,
                    );
                }}
              >
                {terms.labels.home_resume_transfer}
              </Button>
              <Button
                onClick={() => {
                  if (currentScope) acknowledgeTransfer(transfer.id, currentScope);
                }}
              >
                {terms.labels.home_intake_cancel}
              </Button>
            </div>
          }
        />
      ))}
      {!open && files.length > 0 && (
        <Alert
          type="info"
          title={terms.labels.home_intake_pending}
          action={<Button onClick={() => setOpen(true)}>{terms.labels.home_continue}</Button>}
        />
      )}
      {!open && error && <Alert type="warning" title={error} closable onClose={() => setError('')} />}
      <Modal
        key={generation}
        title={terms.labels.home_intake_title}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <p>
          {classification === 'choice'
            ? terms.labels.home_intake_csv
            : classification === 'unsupported'
              ? terms.labels.home_intake_unsupported
              : terms.labels.home_intake_mixed}
        </p>
        <ul className={styles.files}>
          {files.map((file, index) => (
            <li key={index}>
              <span>{file.name}</span>
              <Button
                aria-label={terms.labels.home_intake_remove + ' ' + file.name}
                onClick={() => setFiles((old) => old.filter((_, i) => i !== index))}
              >
                {terms.labels.home_intake_remove}
              </Button>
            </li>
          ))}
        </ul>
        {error && <Alert type="error" title={error} />}
        <div className={styles.actions}>
          <Button onClick={() => send('materials')} disabled={!files.length}>
            {terms.labels.home_intake_materials}
          </Button>
          <Button onClick={() => send('datasets')} disabled={!files.length}>
            {terms.labels.home_intake_datasets}
          </Button>
          <Button
            onClick={() => {
              setFiles([]);
              setOpen(false);
              setError('');
            }}
          >
            {terms.labels.home_intake_cancel}
          </Button>
        </div>
      </Modal>
    </>
  );
}

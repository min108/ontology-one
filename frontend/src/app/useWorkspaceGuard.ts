import { useLayoutEffect, useRef, useState } from 'react';
import { isWorkspaceLeaving, registerWorkspaceGuard } from '../state/workspace-guard';

export function useWorkspaceGuard(dirty: boolean): void {
  const current = useRef(dirty);
  useLayoutEffect(() => {
    current.current = dirty;
  });
  useLayoutEffect(() => {
    let unregister = registerWorkspaceGuard(() => current.current);
    const restore = () => {
      unregister();
      if (!isWorkspaceLeaving()) unregister = registerWorkspaceGuard(() => current.current);
    };
    window.addEventListener('ontology:scope-cleared', restore);
    return () => {
      unregister();
      window.removeEventListener('ontology:scope-cleared', restore);
    };
  }, []);
}

/** Pass only editable business fields. Commit the captured submitted value on success. */
export function useWorkspaceDraftGuard<T>(value: T, initial: T = value): (submitted: T) => void {
  const [baseline, setBaseline] = useState(() => JSON.stringify(initial));
  useWorkspaceGuard(JSON.stringify(value) !== baseline);
  return (submitted) => setBaseline(JSON.stringify(submitted));
}

/** Dialog-owned drafts are discarded when closed; searches must stay outside value. */
export function useWorkspaceDialogGuard(key: string, value: unknown): void {
  const snapshot = JSON.stringify(value);
  const [baseline, setBaseline] = useState({ key, snapshot });
  if (baseline.key !== key) setBaseline({ key, snapshot });
  useWorkspaceGuard(Boolean(key) && baseline.key === key && snapshot !== baseline.snapshot);
}

/** The existing task-start event proves that this exact instruction was accepted. */
export function useWorkspaceInstructionGuard(
  value: string,
  operation: string,
  entityId: string,
): (submitted: string) => void {
  const [submitted, setSubmitted] = useState(value);
  useWorkspaceGuard(Boolean(value.trim()) && value.trim() !== submitted.trim());
  useLayoutEffect(() => {
    const accepted = (event: Event) => {
      const detail: unknown = (event as CustomEvent<unknown>).detail;
      if (
        !detail ||
        typeof detail !== 'object' ||
        !('operation' in detail) ||
        detail.operation !== operation ||
        !('instruction' in detail) ||
        typeof detail.instruction !== 'string' ||
        !('run' in detail) ||
        !detail.run ||
        typeof detail.run !== 'object' ||
        !('entity_id' in detail.run) ||
        detail.run.entity_id !== entityId
      )
        return;
      setSubmitted(detail.instruction);
    };
    window.addEventListener('ontology:harness-run-started', accepted);
    return () => window.removeEventListener('ontology:harness-run-started', accepted);
  }, [operation, entityId]);
  return setSubmitted;
}

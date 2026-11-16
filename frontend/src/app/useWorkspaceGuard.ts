import { useLayoutEffect, useRef } from 'react';
import { registerWorkspaceGuard } from '../state/workspace-guard';

export function useWorkspaceGuard(dirty: boolean): void {
  const current = useRef(dirty);
  useLayoutEffect(() => {
    current.current = dirty;
  });
  useLayoutEffect(() => registerWorkspaceGuard(() => current.current), []);
}

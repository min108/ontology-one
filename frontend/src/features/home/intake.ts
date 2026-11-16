import {
  isWorkspaceConfirmationOpen,
  isWorkspaceLeaving,
  registerWorkspaceGuard,
} from '../../state/workspace-guard';
import { terms } from '../../i18n/zh-CN';
export type HomeScope = { userId: string; tenantId: string; scopeKey: string };
export type IntakeTarget = 'materials' | 'datasets';
export const transferScope = (scope: HomeScope) =>
  JSON.stringify([scope.userId, scope.tenantId, scope.scopeKey]);
const extension = (file: File) => file.name.toLowerCase().split('.').at(-1) ?? '';
const documents = new Set(['txt', 'md', 'pdf', 'docx', 'html', 'htm']);
export function classifyFiles(files: readonly File[]): IntakeTarget | 'choice' | 'mixed' | 'unsupported' {
  const types = new Set(
    files.map((file) =>
      documents.has(extension(file))
        ? 'materials'
        : extension(file) === 'xlsx'
          ? 'datasets'
          : extension(file) === 'csv'
            ? 'choice'
            : 'unsupported',
    ),
  );
  if (!files.length || types.has('unsupported')) return 'unsupported';
  if (types.has('materials') && types.has('datasets')) return 'mixed';
  if (types.has('choice')) return 'choice';
  return types.has('materials') ? 'materials' : types.has('datasets') ? 'datasets' : 'choice';
}
export function fileProblems(files: readonly File[], target: IntakeTarget): string[] {
  const max = target === 'materials' ? 40 : 20,
    size = (target === 'materials' ? 25 : 50) * 1024 * 1024;
  const errors: string[] = [];
  if (!files.length || files.length > max)
    errors.push(target === 'materials' ? terms.labels.home_material_limit : terms.labels.home_dataset_limit);
  for (const file of files)
    if (
      file.size <= 0 ||
      file.size > size ||
      !(target === 'materials'
        ? documents.has(extension(file)) || extension(file) === 'csv'
        : ['xlsx', 'csv'].includes(extension(file)))
    )
      errors.push(file.name + '：' + terms.labels.home_invalid_file);
  return errors;
}
type Transfer = { scope: string; target: IntakeTarget; files: File[]; recipe: boolean; cleanup: () => void };
const transfers = new Map<string, Transfer>();
const notifyTransfers = () => window.dispatchEvent(new Event('ontology:home-transfers'));
export function pendingTransfers(scope: HomeScope) {
  return [...transfers.entries()]
    .filter(([, transfer]) => transfer.scope === transferScope(scope))
    .map(([id, transfer]) => ({
      id,
      target: transfer.target,
      recipe: transfer.recipe,
      files: [...transfer.files],
    }));
}
export function stageFiles(scope: HomeScope, target: IntakeTarget, files: File[], recipe = false): string {
  if (
    !scope.userId ||
    !scope.tenantId ||
    !scope.scopeKey ||
    isWorkspaceLeaving() ||
    isWorkspaceConfirmationOpen() ||
    fileProblems(files, target).length
  )
    throw new Error(terms.labels.home_transfer_unavailable);
  const id = crypto.randomUUID();
  transfers.set(id, {
    scope: transferScope(scope),
    target,
    files: [...files],
    recipe,
    cleanup: registerWorkspaceGuard(() => transfers.has(id)),
  });
  notifyTransfers();
  return id;
}
export function readTransfer(id: string, scope: HomeScope, target: IntakeTarget): { files: File[] } | null {
  const transfer = transfers.get(id);
  return !isWorkspaceLeaving() &&
    !isWorkspaceConfirmationOpen() &&
    transfer?.scope === transferScope(scope) &&
    transfer.target === target
    ? { files: [...transfer.files] }
    : null;
}
export function acknowledgeTransfer(id: string, scope: HomeScope): void {
  const transfer = transfers.get(id);
  if (transfer?.scope !== transferScope(scope)) return;
  transfer.cleanup();
  transfers.delete(id);
  notifyTransfers();
}
export function clearTransfers(): void {
  for (const transfer of transfers.values()) transfer.cleanup();
  transfers.clear();
  notifyTransfers();
}
window.addEventListener('ontology:scope-cleared', clearTransfers);

import type { ElementDetail } from '../../api/generated/types';
export function gates(
  failed: ('QG1' | 'QG2' | 'QG3' | 'QG4' | 'QG5')[] = [],
): NonNullable<ElementDetail['gates']> {
  return {
    passed: failed.length === 0,
    blockers: failed.map(() => '需要补齐依据'),
    checks: (['QG1', 'QG2', 'QG3', 'QG4', 'QG5'] as const).map((code, i) => ({
      code,
      label: ['出处可定位', '口径无歧义', '条件完整', '关系经审定', '版本可还原'][i],
      status: failed.includes(code) ? 'fail' : 'pass',
      reason: failed.includes(code) ? '需要补齐依据' : '检查通过',
      can_waive: code === 'QG1' || code === 'QG3',
      waiver_reason: '',
    })),
  };
}

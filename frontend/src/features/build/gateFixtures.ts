import fixture from '../../../test-fixtures/r4-semantic-content.json';
import type { ElementDetail } from '../../api/generated/types';
export function gates(
  failed: ('QG1' | 'QG2' | 'QG3' | 'QG4' | 'QG5')[] = [],
): NonNullable<ElementDetail['gates']> {
  return {
    passed: failed.length === 0,
    blockers: failed.map(() => fixture.missingEvidence),
    checks: (['QG1', 'QG2', 'QG3', 'QG4', 'QG5'] as const).map((code, i) => ({
      code,
      label: [
        fixture.sourceLabel,
        fixture.definitionLabel,
        fixture.conditionsLabel,
        fixture.relationsLabel,
        fixture.versionLabel,
      ][i],
      status: failed.includes(code) ? 'fail' : 'pass',
      reason: failed.includes(code) ? fixture.missingEvidence : fixture.checkPassed,
      can_waive: code === 'QG1' || code === 'QG3',
      waiver_reason: '',
    })),
  };
}

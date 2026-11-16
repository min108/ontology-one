import type { ReactNode } from 'react';
import { ContextTrail } from '@legacy/navigation';
import { LegacyBlock } from '../ui/LegacyBlock';
import { LegacyOwnership } from './LegacyOwnership';

export function LegacyPage({ children }: { children: ReactNode }) {
  return (
    <LegacyBlock>
      <ContextTrail />
      <LegacyOwnership />
      {children}
    </LegacyBlock>
  );
}

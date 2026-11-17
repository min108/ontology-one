import DemoWorkspace from '@legacy/components/DemoWorkspace';
import { PageLayout } from '../../../ui/PageLayout';
import { PageHeader } from '../../../ui/PageHeader';
import { copy } from '../shared';
import { LegacyBlock } from '../../../ui/LegacyBlock';

/** SET-03 stays with the original governed demo implementation until R3/R9. */
export default function DemoPage() {
  return (
    <PageLayout variant="archive" header={<PageHeader title={copy.admin_demo_title} />}>
      <LegacyBlock>
        <DemoWorkspace />
      </LegacyBlock>
    </PageLayout>
  );
}

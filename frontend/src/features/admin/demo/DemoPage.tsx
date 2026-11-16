import DemoWorkspace from '@legacy/components/DemoWorkspace';
import { PageHeader } from '../../../ui/PageHeader';
import { copy } from '../shared';
import styles from '../Admin.module.css';
import { LegacyBlock } from '../../../ui/LegacyBlock';

/** SET-03 stays with the original governed demo implementation until R3/R9. */
export default function DemoPage() {
  return (
    <div className={styles.page}>
      <PageHeader title={copy.admin_demo_title} />
      <LegacyBlock>
        <DemoWorkspace />
      </LegacyBlock>
    </div>
  );
}

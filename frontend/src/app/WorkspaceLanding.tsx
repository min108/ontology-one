import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { terms } from '../i18n/zh-CN';
import { PageLayout } from '../ui/PageLayout';
import { PageHeader } from '../ui/PageHeader';
import { StateBlock } from '../ui/StateBlock';
import type { RouteEntry } from './navigation';

export function WorkspaceLanding({ entries }: { entries: RouteEntry[] }) {
  const { search, hash } = useLocation();
  const navigate = useNavigate();
  if (entries[0]) return <Navigate replace to={{ pathname: entries[0].href, search, hash }} />;
  return (
    <PageLayout variant="archive" header={<PageHeader title={terms.labels.r3_admin_landing_title} />}>
      <StateBlock
        state="forbidden"
        action={{ label: terms.actions.shell_back_home, onClick: () => navigate('/') }}
      />
    </PageLayout>
  );
}

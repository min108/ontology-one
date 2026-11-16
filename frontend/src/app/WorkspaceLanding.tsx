import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { terms } from '../i18n/zh-CN';
import { StateBlock } from '../ui/StateBlock';
import type { RouteEntry } from './navigation';

export function WorkspaceLanding({ entries }: { entries: RouteEntry[] }) {
  const { search, hash } = useLocation();
  const navigate = useNavigate();
  if (entries[0]) return <Navigate replace to={{ pathname: entries[0].href, search, hash }} />;
  return (
    <StateBlock
      state="forbidden"
      action={{ label: terms.actions.shell_back_home, onClick: () => navigate('/') }}
    />
  );
}

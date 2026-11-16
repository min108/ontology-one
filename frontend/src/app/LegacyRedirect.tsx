import { Navigate, useLocation } from 'react-router-dom';
import { legacyRedirect } from './navigation';

export function LegacyRedirect() {
  const { pathname, search, hash } = useLocation();
  const target = legacyRedirect(pathname, search, hash);
  return target ? <Navigate replace to={target} /> : null;
}

import { lazy, Suspense } from 'react';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { Loading } from '@legacy/components/ui';
import { LegacyBlock } from '../ui/LegacyBlock';
import { StateBlock } from '../ui/StateBlock';
import { terms } from '../i18n/zh-CN';
import { LegacyPage } from './LegacyPage';
import { EntityDossierRoute } from './EntityDossierRoute';
import { LegacyRedirect } from './LegacyRedirect';
import { WorkspaceLanding } from './WorkspaceLanding';
import { primaryNavigation, secondaryNavigation, type NavigationPrincipal } from './navigation';

const Home = lazy(() => import('../features/home/Home'));
const Security = lazy(() => import('../features/account/AccountSecurity'));
const Administration = lazy(() => import('../features/admin/UsersPage'));
const Organizations = lazy(() => import('../features/admin/OrganizationsPage'));
const Audit = lazy(() => import('../features/admin/AuditPage'));
const ModelPage = lazy(() => import('../features/admin/model/ModelPage'));
const BackupPage = lazy(() => import('../features/admin/backup/BackupPage'));
const SystemPage = lazy(() => import('../features/admin/system/SystemPage'));
const DemoPage = lazy(() => import('../features/admin/demo/DemoPage'));
const retained = [
  { path: '/group', component: lazy(() => import('@legacy/pages/Group')) },
  { path: '/briefing', component: lazy(() => import('@legacy/pages/Briefing')) },
  { path: '/experience', component: lazy(() => import('@legacy/pages/Experience')) },
  { path: '/impact', component: lazy(() => import('@legacy/pages/Impact')) },
  { path: '/work', component: lazy(() => import('@legacy/pages/Work')) },
  { path: '/materials', component: lazy(() => import('@legacy/pages/Materials')) },
  { path: '/materials/:id', component: lazy(() => import('@legacy/pages/MaterialDetail')) },
  { path: '/projects', component: lazy(() => import('@legacy/pages/Projects')) },
  { path: '/projects/:id', component: lazy(() => import('@legacy/pages/ProjectDetail')) },
  { path: '/tasks/:id', component: lazy(() => import('@legacy/pages/TaskDetail')) },
  { path: '/assets', component: lazy(() => import('@legacy/pages/Assets')) },
  { path: '/authority', component: lazy(() => import('@legacy/pages/Authority')) },
  {
    path: '/authority/:id',
    component: lazy(async () => ({ default: (await import('@legacy/pages/Authority')).AuthorityDetail })),
  },
  {
    path: '/knowledge/collections/:id',
    component: lazy(async () => ({ default: (await import('@legacy/pages/Knowledge')).KnowledgeCollection })),
  },
  { path: '/datasets', component: lazy(() => import('@legacy/pages/Datasets')) },
  { path: '/datasets/:id', component: lazy(() => import('@legacy/pages/DatasetDetail')) },
  { path: '/data-sources', component: lazy(() => import('@legacy/pages/DataSources')) },
  { path: '/data-sources/:sourceId', component: lazy(() => import('@legacy/pages/DataSources')) },
  { path: '/graph', component: lazy(() => import('@legacy/pages/Graph')) },
  { path: '/applications', component: lazy(() => import('@legacy/pages/Applications')) },
  { path: '/maintenance', component: lazy(() => import('@legacy/pages/Maintenance')) },
  {
    path: '/integration-applications',
    component: lazy(() => import('@legacy/pages/IntegrationApplications')),
  },
  { path: '/skill-development', component: lazy(() => import('@legacy/pages/SkillDevelopment')) },
  { path: '/skill-development/:id', component: lazy(() => import('@legacy/pages/SkillDevelopment')) },
];

// R1-6/7/8 replace these adapters only after their operation checklist passes.
export const completedAdministration = new Set([
  '/admin/users',
  '/admin/orgs',
  '/admin/audit',
  '/admin/model',
  '/admin/backup',
  '/admin/demo',
  '/admin/system',
]);

export function AppRoutes({ principal }: { principal: NavigationPrincipal }) {
  const navigate = useNavigate();
  const forbidden = (
    <StateBlock
      state="forbidden"
      action={{ label: terms.actions.shell_back_home, onClick: () => navigate('/') }}
    />
  );
  return (
    <Suspense
      fallback={
        <LegacyBlock page={false}>
          <Loading />
        </LegacyBlock>
      }
    >
      <Routes>
        <Route path="/" element={<Home />} />
        {primaryNavigation(principal.role)
          .filter((entry) => entry.key !== 'home')
          .map((entry) => (
            <Route
              key={entry.key}
              path={entry.href}
              element={
                <WorkspaceLanding
                  entries={secondaryNavigation(entry.key, principal, completedAdministration)}
                />
              }
            />
          ))}
        {['/security', '/administration', '/settings'].map((path) => (
          <Route key={path} path={path} element={<LegacyRedirect />} />
        ))}
        <Route path="/account/security" element={<Security />} />
        <Route
          path="/admin/users"
          element={
            principal.role === 'admin' && principal.permissions.includes('admin.users') ? (
              <Administration />
            ) : (
              forbidden
            )
          }
        />
        <Route path="/admin/model" element={<ModelPage />} />
        <Route path="/admin/backup" element={<BackupPage />} />
        <Route path="/admin/system" element={<SystemPage />} />
        <Route path="/admin/demo" element={<DemoPage />} />
        <Route path="/admin/orgs" element={<Organizations />} />
        <Route path="/admin/audit" element={<Audit />} />
        <Route
          path="/assets/:id"
          element={
            <LegacyPage>
              <EntityDossierRoute />
            </LegacyPage>
          }
        />
        {retained.map(({ path, component: Page }) => (
          <Route
            key={path}
            path={path}
            element={
              <LegacyPage>
                <Page />
              </LegacyPage>
            }
          />
        ))}
        <Route
          path="*"
          element={
            <StateBlock
              state="empty"
              description={terms.labels.shell_not_found}
              action={{ label: terms.actions.shell_back_home, onClick: () => navigate('/') }}
            />
          }
        />
      </Routes>
    </Suspense>
  );
}

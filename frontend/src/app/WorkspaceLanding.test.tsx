import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { terms } from '../i18n/zh-CN';
import { WorkspaceLanding } from './WorkspaceLanding';
import { secondaryNavigation } from './navigation';

function CurrentUrl() {
  const location = useLocation();
  return <output>{location.pathname + location.search + location.hash}</output>;
}

describe('workspace root entry', () => {
  it('opens the first permitted completed page while retaining URL state', async () => {
    const entries = secondaryNavigation(
      'admin',
      { role: 'reviewer', superadmin: false, permissions: [] },
      new Set(['/admin/users', '/admin/model', '/admin/system']),
    );
    render(
      <MemoryRouter initialEntries={['/admin?tab=health#connection']}>
        <Routes>
          <Route path="/admin" element={<WorkspaceLanding entries={entries} />} />
          <Route path="/admin/model" element={<CurrentUrl />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole('status')).toHaveTextContent('/admin/model?tab=health#connection');
  });
  it('provides a return action when there is no permitted page', () => {
    render(
      <MemoryRouter>
        <WorkspaceLanding entries={[]} />
      </MemoryRouter>,
    );
    expect(screen.getByRole('button', { name: terms.actions.shell_back_home })).toBeInTheDocument();
  });
});

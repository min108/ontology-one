import { StrictMode, useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { useIncomingFiles } from './useIncomingFiles';
import { HomeIntake } from './HomeIntake';
import { stageFiles, clearTransfers, readTransfer } from './intake';
import { useScopeStore } from '../../state/scope';
import { useSessionStore } from '../../state/session';
import { useWorkspaceGuard } from '../../app/useWorkspaceGuard';
import { readWorkspaceGuards, setWorkspaceTransition } from '../../state/workspace-guard';
import { terms } from '../../i18n/zh-CN';
const scope = { userId: 'u', tenantId: 'a', scopeKey: 'local' };
beforeEach(() => {
  clearTransfers();
  setWorkspaceTransition('idle');
  useScopeStore.getState().setScope('a');
  useSessionStore.setState({
    session: {
      authenticated: true,
      user: {
        id: 'u',
        username: 'one',
        display_name: 'One',
        home_tenant_id: 'a',
        role: 'modeler',
        is_superadmin: false,
        must_change_password: false,
      },
      workspace: { tenant_id: 'a', name: 'A', category: 'local', readonly: false },
      workspaces: [],
      roles: [],
      permissions: { can_edit: true, can_review: false, manage_users: false, manage_grants: false },
      csrf_token: 'c',
      must_change_password: false,
    },
  });
});
afterEach(() => clearTransfers());
function Receiver() {
  const [files, setFiles] = useState<File[]>([]);
  useWorkspaceGuard(files.length > 0);
  useIncomingFiles('materials', (incoming) => {
    setFiles((old) => [...old, ...incoming]);
    return true;
  });
  return (
    <ul data-testid="received">
      {files.map((file, i) => (
        <li key={i}>{file.name}</li>
      ))}
    </ul>
  );
}
function Location() {
  return <output data-testid="location">{useLocation().pathname + useLocation().search}</output>;
}
it('StrictMode receives exactly once and keeps the queue guarded', async () => {
  const id = stageFiles(scope, 'materials', [new File(['a'], 'one.md')]);
  window.history.replaceState(null, '', '/materials?upload=1&intake=' + id);
  render(
    <StrictMode>
      <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
        <Receiver />
      </MemoryRouter>
    </StrictMode>,
  );
  expect(await screen.findAllByText('one.md')).toHaveLength(1);
  expect(readTransfer(id, scope, 'materials')).toBeNull();
  expect(readWorkspaceGuards().dirtyCount).toBeGreaterThan(0);
});
it('a cancelled CSV chooser preserves its files until explicit discard', async () => {
  render(
    <MemoryRouter>
      <HomeIntake />
    </MemoryRouter>,
  );
  fireEvent.drop(document, { dataTransfer: { types: ['Files'], files: [new File(['a,b'], 'one.csv')] } });
  expect(await screen.findByText('one.csv')).toBeInTheDocument();
  expect(screen.getByText(terms.labels.home_intake_csv)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(await screen.findByText(terms.labels.home_intake_pending)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: terms.labels.home_continue }));
  expect(await screen.findByText('one.csv')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: terms.labels.home_intake_cancel }));
  await waitFor(() => expect(readWorkspaceGuards().dirtyCount).toBe(0));
});
it('moves the selected mixed-file group and keeps the other files available', async () => {
  render(
    <MemoryRouter>
      <HomeIntake />
      <Location />
    </MemoryRouter>,
  );
  fireEvent.drop(document, {
    dataTransfer: { types: ['Files'], files: [new File(['a'], 'one.md'), new File(['b'], 'two.xlsx')] },
  });
  expect(await screen.findByText('two.xlsx')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: terms.labels.home_intake_materials }));
  expect(screen.getByTestId('location')).toHaveTextContent('/materials?upload=1&intake=');
  fireEvent.click(await screen.findByRole('button', { name: terms.labels.home_continue }));
  expect(await screen.findByText('two.xlsx')).toBeInTheDocument();
  expect(within(screen.getByRole('dialog')).queryByText('one.md')).not.toBeInTheDocument();
});
it('unsupported files stay visible and scope clearing releases all pending file memory', async () => {
  render(
    <MemoryRouter>
      <HomeIntake />
    </MemoryRouter>,
  );
  fireEvent.drop(document, { dataTransfer: { types: ['Files'], files: [new File(['a'], 'one.exe')] } });
  expect(await screen.findByText('one.exe')).toBeInTheDocument();
  expect(screen.getByText(terms.labels.home_intake_unsupported)).toBeInTheDocument();
  act(() => {
    setWorkspaceTransition('leaving');
    window.dispatchEvent(new Event('ontology:scope-cleared'));
  });
  await waitFor(() => expect(screen.queryByText('one.exe')).not.toBeInTheDocument());
});

it('does not consume staged files after edit permission is removed', () => {
  const id = stageFiles(scope, 'materials', [new File(['a'], 'private.md')]);
  const session = useSessionStore.getState().session!;
  useSessionStore.setState({ session: { ...session, workspace: { ...session.workspace, readonly: true } } });
  window.history.replaceState(null, '', '/materials?intake=' + id);
  render(
    <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
      <Receiver />
    </MemoryRouter>,
  );
  expect(screen.queryByText('private.md')).not.toBeInTheDocument();
  expect(readTransfer(id, scope, 'materials')?.files).toHaveLength(1);
});

it('keeps an unacknowledged handoff recoverable after navigation', async () => {
  stageFiles(scope, 'materials', [new File(['a'], 'retained.md')]);
  render(
    <MemoryRouter initialEntries={['/projects']}>
      <HomeIntake />
      <Location />
    </MemoryRouter>,
  );
  expect(await screen.findByText('retained.md')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: terms.labels.home_resume_transfer }));
  expect(screen.getByTestId('location')).toHaveTextContent('/materials?upload=1&intake=');
  fireEvent.click(screen.getByRole('button', { name: terms.labels.home_intake_cancel }));
  expect(screen.queryByText('retained.md')).not.toBeInTheDocument();
  expect(readWorkspaceGuards().dirtyCount).toBe(0);
});

it('does not carry an unchosen file batch into another scope', async () => {
  render(
    <MemoryRouter>
      <HomeIntake />
    </MemoryRouter>,
  );
  fireEvent.drop(document, { dataTransfer: { types: ['Files'], files: [new File(['a'], 'scope.csv')] } });
  expect(await screen.findByText('scope.csv')).toBeInTheDocument();
  act(() => useScopeStore.getState().setScope('a', 'group'));
  expect(screen.queryByText('scope.csv')).not.toBeInTheDocument();
  expect(readWorkspaceGuards().dirtyCount).toBe(0);
});

it('defers a late receiver while confirming and resumes once only after explicit retry', async () => {
  const id = stageFiles(scope, 'materials', [new File(['a'], 'waiting.md')]);
  setWorkspaceTransition('confirming');
  window.history.replaceState(null, '', '/materials?upload=1&intake=' + id);
  render(
    <StrictMode>
      <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
        <HomeIntake />
        <Receiver />
      </MemoryRouter>
    </StrictMode>,
  );
  expect(within(screen.getByTestId('received')).queryByText('waiting.md')).not.toBeInTheDocument();
  expect(readTransfer(id, scope, 'materials')).toBeNull();
  setWorkspaceTransition('idle');
  expect(readTransfer(id, scope, 'materials')?.files).toHaveLength(1);
  expect(within(screen.getByTestId('received')).queryByText('waiting.md')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: terms.labels.home_resume_transfer }));
  expect(await within(screen.getByTestId('received')).findAllByText('waiting.md')).toHaveLength(1);
  expect(readTransfer(id, scope, 'materials')).toBeNull();
});

it('preserves the staged file batch when an editor rejects navigation and can resume explicitly', async () => {
  let refuse = true;
  const guard = (event: Event) => {
    if (refuse) event.preventDefault();
  };
  window.addEventListener('ontology:request-route-navigation', guard);
  try {
    render(
      <MemoryRouter>
        <HomeIntake />
        <Location />
      </MemoryRouter>,
    );
    fireEvent.drop(document, {
      dataTransfer: { types: ['Files'], files: [new File(['retained'], 'guarded.md')] },
    });
    expect(await screen.findByText('guarded.md')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/);
    expect(readWorkspaceGuards().dirtyCount).toBeGreaterThan(0);
    refuse = false;
    fireEvent.click(screen.getByRole('button', { name: terms.labels.home_resume_transfer }));
    expect(screen.getByTestId('location')).toHaveTextContent('/materials?upload=1&intake=');
    expect(screen.getByText('guarded.md')).toBeInTheDocument();
  } finally {
    window.removeEventListener('ontology:request-route-navigation', guard);
  }
});

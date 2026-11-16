import { StrictMode, type ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Graph from '@legacy/pages/Graph';
import { setWorkspaceTransition } from '../state/workspace-guard';

type Viewport = { x: number; y: number; zoom: number };
const { flow, request } = vi.hoisted(() => ({
  flow: { onMoveEnd: undefined as undefined | ((event: null, viewport: Viewport) => void) },
  request: vi.fn(),
}));
vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ onMoveEnd, children }: { onMoveEnd: typeof flow.onMoveEnd; children: ReactNode }) => {
    flow.onMoveEnd = onMoveEnd;
    return <div data-testid="flow">{children}</div>;
  },
  Background: () => null,
  MiniMap: () => null,
  Handle: () => null,
  BaseEdge: () => null,
  EdgeLabelRenderer: () => null,
  useStore: () => 1,
  Position: {},
  MarkerType: {},
}));
vi.mock('@legacy/api', () => ({ api: request, statusLabel: (value: string) => value }));
vi.mock('@legacy/auth', () => ({
  useAuth: () => ({ session: { permissions: { can_edit: true, can_review: true } } }),
}));
const viewport = { x: 12, y: 34, zoom: 0.8 };
beforeEach(() => {
  flow.onMoveEnd = undefined;
  setWorkspaceTransition('idle');
  request.mockImplementation(async (path: string) => {
    if (path.startsWith('/data-instances/') && !path.includes('/graph?'))
      return { filters: { organizations: [], datasets: [] } };
    return {
      nodes: [],
      edges: [],
      view: 'panorama',
      title: 'Graph',
      facets: { domains: [], kinds: [], relation_types: [] },
      total: 1,
      summary: { units: 1 },
      source_counts: { user: 1, demo: 0 },
      sources: [{ source_id: 'field', samples: [] }],
    };
  });
});
afterEach(() => setWorkspaceTransition('idle'));

async function openGraph(instances: boolean) {
  window.history.replaceState(null, '', instances ? '/graph?view=instances&node=data' : '/graph');
  render(
    <StrictMode>
      <BrowserRouter>
        <Link to="/build">Leave graph</Link>
        <Link to={instances ? '/graph' : '/graph?view=instances&node=data'}>Change graph</Link>
        <Routes>
          <Route path="/graph" element={<Graph />} />
          <Route path="/build" element={<div>Build page</div>} />
        </Routes>
      </BrowserRouter>
    </StrictMode>,
  );
  await screen.findByTestId('flow');
  await waitFor(() => expect(request).toHaveBeenCalled());
  return flow.onMoveEnd!;
}

describe.each([false, true])('graph viewport ownership (instances: %s)', (instances) => {
  it('keeps saving the mounted graph viewport', async () => {
    const complete = await openGraph(instances);
    act(() => complete(null, viewport));
    expect(window.location.pathname).toBe('/graph');
    expect(new URLSearchParams(window.location.search).get(instances ? 'ivz' : 'vz')).toBe('0.800');
  });
  it('ignores a captured animation callback after navigating away', async () => {
    const complete = await openGraph(instances);
    fireEvent.click(screen.getByText('Leave graph'));
    expect(screen.getByText('Build page')).toBeInTheDocument();
    act(() => complete(null, viewport));
    expect(window.location.pathname + window.location.search).toBe('/build');
  });
  it('cannot add the previous graph viewport to the replacement graph', async () => {
    const complete = await openGraph(instances);
    fireEvent.click(screen.getByText('Change graph'));
    const target = window.location.pathname + window.location.search;
    act(() => complete(null, viewport));
    expect(window.location.pathname + window.location.search).toBe(target);
  });
  it('does not save after the workspace is leaving even before unmount', async () => {
    const complete = await openGraph(instances);
    const target = window.location.pathname + window.location.search;
    setWorkspaceTransition('leaving');
    act(() => complete(null, viewport));
    expect(window.location.pathname + window.location.search).toBe(target);
  });
});

import { lazy, StrictMode, Suspense, useContext, type ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter, Link, Route, Routes, UNSAFE_LocationContext, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Graph from '@legacy/pages/Graph';
import InstanceGraph from '@legacy/pages/InstanceGraph';
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

async function openGraph(instances: boolean, suspend = false) {
  const Pending = lazy(() => new Promise<{ default: () => ReactNode }>(() => {}));
  function GraphRoute() {
    const location = useLocation();
    const nextInstances = new URLSearchParams(location.search).get('view') === 'instances';
    return suspend && nextInstances !== instances ? <Pending /> : <Graph />;
  }
  window.history.replaceState(null, '', instances ? '/graph?view=instances&node=data' : '/graph');
  render(
    <StrictMode>
      <BrowserRouter>
        <Link to="/build">Leave graph</Link>
        <Link to={instances ? '/graph' : '/graph?view=instances&node=data'}>Change graph</Link>
        <Suspense fallback={<div>Pending page</div>}>
          <Routes>
            <Route path="/graph" element={<GraphRoute />} />
            <Route path="/build" element={suspend ? <Pending /> : <div>Build page</div>} />
          </Routes>
        </Suspense>
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
  it.each(['Leave graph', 'Change graph'])(
    'keeps the destination while %s suspends with the old graph visible',
    async (link) => {
      const complete = await openGraph(instances, true);
      const previousCanvas = screen.getByTestId('flow');
      fireEvent.click(screen.getByText(link));
      const target = window.location.pathname + window.location.search;
      expect(target).toBe(
        link === 'Leave graph' ? '/build' : instances ? '/graph' : '/graph?view=instances&node=data',
      );
      expect(screen.getByTestId('flow')).toBe(previousCanvas);
      expect(previousCanvas).toBeVisible();
      expect(screen.queryByText('Pending page')).not.toBeInTheDocument();
      act(() => complete(null, viewport));
      expect(window.location.pathname + window.location.search).toBe(target);
    },
  );
  it('retains its original route ownership when Location updates before the route match', async () => {
    const initial = instances ? '/graph?view=instances&node=data' : '/graph';
    const target = instances ? '/build?view=instances&node=data' : '/build';
    const Page = instances ? InstanceGraph : Graph;
    function RetainedRoute() {
      const currentLocation = useContext(UNSAFE_LocationContext);
      // Reproduce the transition boundary: the old route match still owns relative
      // navigation while its retained child observes the destination Location.
      return (
        <Routes location={initial}>
          <Route
            path="/graph"
            element={
              <UNSAFE_LocationContext.Provider value={currentLocation}>
                <output data-testid="observed-path">{currentLocation.location.pathname}</output>
                <Page />
              </UNSAFE_LocationContext.Provider>
            }
          />
        </Routes>
      );
    }
    window.history.replaceState(null, '', initial);
    render(
      <StrictMode>
        <BrowserRouter>
          <Link to={target}>Move location</Link>
          <RetainedRoute />
        </BrowserRouter>
      </StrictMode>,
    );
    const previousCanvas = await screen.findByTestId('flow');
    fireEvent.click(screen.getByText('Move location'));
    expect(screen.getByTestId('observed-path')).toHaveTextContent('/build');
    expect(screen.getByTestId('flow')).toBe(previousCanvas);
    expect(previousCanvas).toBeVisible();
    // Use the callback from the retained component's NEW render, not only the
    // closure captured before navigation: that is the browser regression.
    act(() => flow.onMoveEnd!(null, viewport));
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

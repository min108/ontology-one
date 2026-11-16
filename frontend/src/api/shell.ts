import { api } from './client';

export interface EntityRoute {
  kind: string;
  project_id?: string;
  matter_id?: string;
}
export interface AssistantStatus {
  online_verified: boolean;
  configured: boolean;
}
export function readEntityRoute(id: string, signal?: AbortSignal): Promise<EntityRoute> {
  return api<EntityRoute>('/entities/' + encodeURIComponent(id) + '/route', { signal });
}
export function readAssistantStatus(signal?: AbortSignal): Promise<AssistantStatus> {
  return api<AssistantStatus>('/ai/status', { signal });
}

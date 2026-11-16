export type Visibility = 'institution' | 'headquarters';

export type FederatedEntity = {
  id: string;
  node_id: string;
  kind: string;
  title: string;
  summary: string;
  domain: string;
  status: string;
  version: number;
  tenant_id: string;
  tenant_name: string;
  org_id: string;
  visibility: Visibility;
  detail_url?: string;
  payload?: Record<string, unknown>;
};

export type FederationTenant = {
  tenant_id: string;
  tenant_name: string;
  org_id: string;
  tenant_type: string;
  access_mode: 'own' | 'all' | 'headquarters';
  counts: {entities: number; assets: number; relations: number};
  provisioned: boolean;
};

export type FederationAvailability = {
  partial: boolean;
  unavailable_tenants?: string[];
};

export type FederationOverview = FederationAvailability & {
  scope: {
    tenant_id: string;
    tenant_name: string;
    can_global_read: boolean;
    can_create_links: boolean;
  };
  totals: {entities: number; assets: number; relations: number; tenants: number};
  tenants: FederationTenant[];
};

export type FederationSearch = FederationAvailability & {
  items: FederatedEntity[];
  total: number;
  page: number;
  page_size: number;
};

export type FederationGraph = FederationAvailability & {
  roots: {id: string; label: string; kind: 'tenant'; tenant_id: string; tenant_name: string; org_id: string}[];
  nodes: {id: string; entity_id: string; label: string; kind: string; status?: string; visibility?: Visibility; tenant_id: string; tenant_name: string; org_id: string; parent_id: string | null}[];
  edges: {id: string; source: string; target: string; label: string; type: string; cross_tenant: boolean; status?: string}[];
  truncated: boolean;
};

export type FederatedRelation = {
  id: string;
  source: string;
  target: string;
  source_title?: string;
  target_title?: string;
  source_tenant_id?: string;
  target_tenant_id?: string;
  source_entity_id?: string;
  target_entity_id?: string;
  source_tenant_name?: string;
  target_tenant_name?: string;
  link_id?: string;
  label: string;
  type: string;
  cross_tenant?: boolean;
  status?: string;
};

export type FederationDetail = {
  entity: FederatedEntity;
  relations: {incoming: FederatedRelation[]; outgoing: FederatedRelation[]};
  can_set_visibility: boolean;
  visibility_options: Visibility[];
  hidden_reference_count: number;
};

export const visibilityLabel = (value: string) => value === 'headquarters' ? '总部范围公开' : '仅本机构';
export const federationEntityPath = (tenantId: string, entityId: string) =>
  `/federation/entities/${encodeURIComponent(tenantId)}/${encodeURIComponent(entityId)}`;

"""Idempotent control schema and directory installation using a migration account."""
from pathlib import Path
import argparse,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.security.store import initialize_control,list_tenants
from backend.federation import initialize_federation
from backend.jobs import ensure_queue
from backend.db import ensure_tenant_schema
from backend.maintenance import runtime_guard

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--provision-all',action='store_true',help='同时初始化51个机构的空业务库。')
    args=parser.parse_args()
    with runtime_guard(shared=False):
        initialize_control();initialize_federation();ensure_queue()
        if args.provision_all:
            for tenant in list_tenants():ensure_tenant_schema(tenant['id'])
    print('0.5.0账户目录、审计约束与后台队列初始化完成。')

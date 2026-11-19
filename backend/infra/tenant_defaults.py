"""Directory-owned initial state shared by provisioning and empty-workspace recovery."""

from collections.abc import Mapping
from datetime import UTC, datetime


def common_part_name(institution_name: str) -> str:
    return institution_name + "公共部分"


def initial_common_part(tenant: Mapping) -> dict:
    # The common part belongs to this institution from its creation. This
    # durable timestamp also identifies the untouched provisioned row on restore.
    timestamp = datetime.fromtimestamp(tenant["created_at"], UTC).isoformat()
    return {
        "id": "MOD-DEFAULT",
        "module_key": "default",
        "module_type": "org_common",
        "name": common_part_name(tenant["name"]),
        "domain_code": "general",
        "owner_user_id": None,
        "scope_note": None,
        "status": "active",
        "sharing_level": 0,
        "preferences": None,
        "current_release_id": None,
        "created_by": "system",
        "created_at": timestamp,
        "updated_at": timestamp,
    }

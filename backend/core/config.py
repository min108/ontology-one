"""Non-secret defaults for the versioned API and capability gateway."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApiConfig:
    default_page_size: int = 30
    maximum_page_size: int = 200
    maximum_result_bytes: int = 262144
    capability_timeout_seconds: int = 30


CONFIG = ApiConfig()

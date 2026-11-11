"""Application version shared by services, migration and distribution tools."""
from pathlib import Path
import re


def read_version(root=None):
    path = (Path(root) if root is not None else Path(__file__).resolve().parents[1]) / 'VERSION'
    value = path.read_text(encoding='ascii').strip()
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError('VERSION must contain a stable semantic version')
    return value


APP_VERSION = read_version()

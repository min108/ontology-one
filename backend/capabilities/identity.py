"""Register the four R1 identity capabilities before composing REST contracts."""

import importlib

for domain in ("org", "user", "grant", "audit"):
    importlib.import_module("backend.capabilities." + domain)

"""Run the persistent task workers; API and worker share control DB settings."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.jobs import worker_loop
from backend.maintenance import runtime_guard

if __name__ == '__main__':
    try:
        with runtime_guard(shared=True):
            asyncio.run(worker_loop())
    except KeyboardInterrupt:
        pass

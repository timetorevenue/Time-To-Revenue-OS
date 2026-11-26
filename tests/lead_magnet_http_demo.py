"""Serve the real TTROS API against invented local fixture records only.

Usage: PYTHONPATH=tools:dashboard/backend:. python -m tests.lead_magnet_http_demo ROOT PORT
The production lifespan is disabled so David and connectors do not start.
"""

import sys
from pathlib import Path

import uvicorn

from tests.command_centre_fixture import crm_environment
import main as backend


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    environment = crm_environment(root)
    environment.__enter__()
    backend.BASE_DIR = root
    backend.RUNNER = None
    uvicorn.run(backend.app, host="127.0.0.1", port=int(sys.argv[2]), lifespan="off", log_level="warning")

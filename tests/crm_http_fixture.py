"""JSON-lines HTTP bridge; no listener/runtime. Revisit: when CRM UI contracts change."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.command_centre_fixture import crm_environment, NOW
import aos_crm
import main as backend
from fastapi.testclient import TestClient


def main():
    with tempfile.TemporaryDirectory(prefix="ttros-crm-ui-") as folder:
        root = Path(folder)
        with crm_environment(root), patch.object(backend, "BASE_DIR", root), \
                patch.dict(os.environ, {"TTROS_SUBSCRIBERS_ENABLED": "true", "TTROS_LEAD_MAGNET_CAPTURE_TOKEN": "ui-fixture-token"}), \
                patch.object(aos_crm, "_now", return_value=NOW), \
                patch.object(backend, "start_david", side_effect=AssertionError("runtime forbidden")), \
                patch("aos_jobs.create", side_effect=AssertionError("job execution forbidden")):
            client = TestClient(backend.app)  # no context manager: never run production lifespan
            for line in sys.stdin:
                request = json.loads(line)
                response = client.request(request["method"], request["path"], json=request.get("body"), headers=request.get("headers") or {})
                print(json.dumps({"status": response.status_code, "body": response.json()}), flush=True)


if __name__ == "__main__":
    main()

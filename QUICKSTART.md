# Quickstart

Requirements: Linux, macOS or WSL; Python 3.12+; Node.js 22+ for the frontend; Git. No account or API key is needed for steps 1 to 4.

```bash
git clone https://github.com/timetorevenue/Time-To-Revenue-OS.git
cd Time-To-Revenue-OS
```

## 1. Create an isolated Python environment
```bash
python3 -m venv .venv
.venv/bin/pip install -r dashboard/backend/requirements.txt pytest
```

## 2. Create an empty company
```bash
export TTROS_BRAIN_ROOT="$HOME/ttros/brain"
.venv/bin/python tools/ttros_init.py init
.venv/bin/python tools/ttros_init.py status
```
`init` creates an empty, Git-backed Business Brain and empty runtime state. It writes no company facts, accounts or sample
data and is safe to run twice. `status` lists what is still unconfigured.

## 3. Check universal knowledge
```bash
.venv/bin/python -c "import sys; sys.path.insert(0, 'tools'); import universal_knowledge as u; print([m.id for m in u.find('define ideal customer profile')])"
```
Expected: `['universal.marketing.icp-definition']`.

## 4. Start the dashboard (local only)
```bash
cd dashboard/backend && ../../.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8010
# in another terminal
cd dashboard/frontend && npm install && npm run dev
```
Open http://127.0.0.1:3010. `GET http://127.0.0.1:8010/api/setup/status` reports onboarding state. Never bind to a public interface.

## 5. Configure your site (optional)
```bash
export TTROS_STUDIO_SITE_ID=my-site        # key in context/website_studio_sites.json
export TTROS_SITE_DOMAIN=example.com
```

## 6. Run the tests
```bash
scripts/run_core_tests.sh tests/test_core_clean_install.py tests/test_core_separation.py tests/test_site_config.py tests/test_contribution_gate.py
(cd dashboard/frontend && npm test)
```
The full suite: `scripts/run_core_tests.sh` (uses an isolated HOME). See README for known failures.

Model calls are never made by the tests: a guard in `conftest.py` blocks launching real model CLIs. To connect a model
or an account, use the dashboard's Connections view; credentials stay in your environment, never in the repository.

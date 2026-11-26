"""Local-only Studio proposal server, without dashboard lifespan or job recovery.
Run: PYTHONPATH=tools:dashboard/backend .venv/bin/python -m uvicorn studio_local:app --host 127.0.0.1 --port 4319
Use TTROS_STUDIO_API_TARGET=http://127.0.0.1:4319 for the Vite proof.
"""
from pathlib import Path
import os
import tempfile
from contextlib import asynccontextmanager
from fastapi import FastAPI
from aos_david import David
from website_studio import router
# ModelPort writes usage to this isolated root; model routing still uses its existing defaults.
local_root = Path(os.environ['TTROS_STUDIO_STATE']) if os.environ.get('TTROS_STUDIO_STATE') else Path(tempfile.mkdtemp(prefix='ttros-studio-'))
david = None

def get_david():
    global david
    if os.environ.get('TTROS_STUDIO_OFFLINE') == '1':
        raise RuntimeError('Offline demonstration: model calls are disabled. Browser tests use mocked proposals.')
    if david is None:
        david = David(local_root, warm=True)
    return david

@asynccontextmanager
async def lifespan(app):
    yield
    if david is not None:
        david.close()

app = FastAPI(lifespan=lifespan)
app.include_router(router(get_david))

@app.get('/api/website/studio/local-mode')
def local_mode():
    return {'offline': os.environ.get('TTROS_STUDIO_OFFLINE') == '1', 'model_calls': 0}

from product_launch import router as product_launch_router
launch_root = Path(os.environ.get("TTROS_LAUNCH_ROOT", str(local_root)))
app.include_router(product_launch_router(lambda: launch_root))
from dashboard_identity import Identity, install_session_boundary, router as identity_router
from account_connections import router as connections_router
from current_website import router as current_website_router
identity = Identity(local_root, local=True)
install_session_boundary(app, identity)
app.include_router(identity_router(identity))
app.include_router(connections_router(identity))
app.include_router(current_website_router(identity))
# The same operator/CSRF-gated saved-version preview builds as the Dashboard (local preview identity only).
from website_studio_builds import router as builds_router
from website_studio_sources import Sources
app.include_router(builds_router(Sources(), identity))
# Releases: isolated test targets only (TTROS_STUDIO_RELEASE_TARGETS); production Vercel stays blocked.
from website_studio_releases import router as releases_router
app.include_router(releases_router(Sources(), identity))
# Live-source visual editor (snapshot, versions, autosave, assets, real Next builds).
from website_studio_live import router as live_router
_live = live_router(Sources(), identity)
app.include_router(_live)
# Website Manager panel: jobs, receipts and token usage stay in the isolated local root; agent packages
# come from this checkout. Model calls only with TTROS_STUDIO_MANAGER_MODEL=1 (else the model route is refused).
from website_studio_manager import router as manager_router
_manager_port = None
if os.environ.get('TTROS_STUDIO_MANAGER_MODEL') != '1':
    def _manager_port(*args, **kwargs):
        return {'status': 'refused', 'failure_class': 'preflight_refused', 'detail': 'local proof: Website Manager model calls disabled',
                'usage': {'available': False}, 'json': None, 'text': ''}
app.include_router(manager_router(Sources(), identity, _live.live, jobs_root=local_root, package_root=Path(__file__).resolve().parents[2],
                                  modelport=_manager_port))

# Local offline acceptance uses the real David/Jobs/CRM functions without production lifespan,
# worker recovery, notifications, model calls or credential loading.
if os.environ.get('TTROS_STUDIO_OFFLINE') == '1':
    from pydantic import BaseModel, Field
    from fastapi import HTTPException, Request
    import aos_jobs
    import aos_crm

    class LocalDavidRequest(BaseModel):
        text: str = Field(min_length=1, max_length=4000)
        conversation_id: str = 'dashboard-launch'

    @app.post('/api/dashboard/ask-david')
    def offline_ask_david(body: LocalDavidRequest, req: Request):
        def denied(*args, **kwargs):
            raise ValueError('Offline local acceptance: model calls disabled')
        instance = David(launch_root, model=denied, warm=False)
        try:
            context = None
            if body.text == '/connections' or body.text.startswith('/connection '):
                identity.csrf(req)
                from ttros_connections import Connections
                context = (Connections(identity), identity.principal(req))
            return instance.turn(body.conversation_id, body.text, connection_context=context)
        finally:
            instance.close()

    @app.get('/api/health')
    def local_health():
        return {'status': 'ok', 'mode': 'isolated_local_preview', 'david': {'ready': True, 'models': 'disabled'}, 'production': 'untouched'}

    @app.get('/api/website/projects')
    def local_websites():
        import aos_website
        return {'projects': aos_website.list_projects(Path(__file__).resolve().parents[2]), 'jobs': []}

    @app.get('/api/website/preparations')
    def local_preparations():
        import aos_website_prepare
        return {'preparations': aos_website_prepare.list_orders(launch_root)}

    @app.post('/api/website/preparations/seo-plan')
    def local_seo_plan(body: dict):
        import aos_website_seo
        return aos_website_seo.build_plan(body)

    @app.get('/api/david/jobs')
    def local_jobs():
        return {'jobs': aos_jobs.list_jobs(launch_root)}

    @app.get('/api/david/jobs/{job_id}')
    def local_job(job_id: str):
        return aos_jobs.load(launch_root, job_id)

    @app.get('/api/crm/overview')
    @app.get('/api/crm')
    def local_crm():
        return aos_crm.project(launch_root)

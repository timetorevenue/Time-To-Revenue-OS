"""HTTP bindings; the request principal is never accepted in a body."""
import json
import jsonschema
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from ttros_connections import Connections, ConnectionError
from aos_agents.package import PackageError


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PrivateInputRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_input(req):
            try:
                return await handler(req)
            except RequestValidationError:
                # FastAPI's default 422 includes raw input, including project
                # keys when an unexpected field or malformed body is supplied.
                raise HTTPException(422, 'Invalid connection request. Sensitive values were withheld.') from None

        return private_input


class Authorization(Strict):
    service: str
    # Chosen from the app's Composio auth schemes; optional (TTROS picks the simplest).
    scheme: str = Field(default='', max_length=40)
    replace: str | None = Field(default=None, max_length=80)
    resource: str | None = Field(default=None, max_length=120)
    # Write-only credential fields from the secure form. Passed straight to Composio,
    # never stored, logged or returned. No schema constraint: a 422 would echo input.
    fields: dict[str, SecretStr] = Field(default_factory=dict)
    config_fields: dict[str, SecretStr] = Field(default_factory=dict)

    def connect(self, hub, who):
        return hub.connect_app(who, self.service, scheme=self.scheme or None, replace=self.replace, resource=self.resource,
                               fields={k: v.get_secret_value() for k, v in self.fields.items()},
                               config_fields={k: v.get_secret_value() for k, v in self.config_fields.items()})


class ProjectLink(Strict):
    mode: str = Field(pattern='^(customer|managed)$')
    # Write-only: verified, sealed server-side and never returned or logged.
    # No schema constraint here: a 422 would echo the input. Format is checked in code.
    api_key: SecretStr = SecretStr('')
    composio_user_id: str = Field(default='', max_length=128)


class ProjectBinding(Strict):
    account_id: str = Field(max_length=80)
    service: str | None = Field(default=None, max_length=80)
    replace: str | None = Field(default=None, max_length=80)
    resource: str | None = Field(default=None, max_length=120)


class Custom(Strict):
    name: str = Field(min_length=1, max_length=100)
    service: str
    host: str
    auth: str = 'none'
    secret_ref: str = ''
    operations: list[dict] = Field(default_factory=list, max_length=20)
    openapi: dict | None = None
    custom_required: bool = False


class Execute(Strict):
    operation: str
    arguments: dict = Field(default_factory=dict)


class Access(Strict):
    mode: str
    agents: dict[str, list[str]] = Field(default_factory=dict)


class AgentTask(Strict):
    input: dict = Field(default_factory=dict)
    permissions: list[tuple[str, str]] = Field(max_length=20)


def router(identity, hub=None, get_runner=None):
    hub = hub or Connections(identity)
    api = APIRouter(prefix='/hub', route_class=PrivateInputRoute)

    def invoke(req, fn, *, mutate=False):
        if mutate:
            identity.csrf(req)
        who = identity.principal(req)
        try:
            return fn(who)
        except (ConnectionError, PackageError) as exc:
            raise HTTPException(409, str(exc)) from None
        except (jsonschema.ValidationError, jsonschema.SchemaError, ValueError, TypeError, KeyError):
            raise HTTPException(422, 'Connection or operation schema is invalid. Sensitive values were withheld.') from None

    @api.get('/state')
    def state(req: Request):
        def get(who):
            project, project_accounts, project_blocker = hub.project_state(who), [], None
            if project and project['status'] == 'LINKED':
                try:
                    project_accounts = hub.project_accounts(who)
                except ConnectionError as exc:
                    project_blocker = str(exc)
            return {'connections': hub.rows(who),
                    'project': project, 'project_accounts': project_accounts, 'project_blocker': project_blocker,
                    'receipts': hub.receipts(who), 'authority': 'rules/runtime_permission_policy.md'}
        return invoke(req, get)

    @api.get('/agents')
    def agents(req: Request):
        def get(who):
            from aos_agents.package import load_package
            root = identity.path.parents[2]
            packages = []
            directory = root / 'agents/packages'
            for folder in sorted(directory.iterdir()) if directory.is_dir() else []:
                if not folder.is_dir() or not (folder / 'agent.toml').is_file():
                    continue
                try:
                    package = load_package(folder.name, root)
                except PackageError:
                    continue
                packages.append({'ref': package.ref, 'name': package.name, 'version': package.version,
                                 'grants': sorted(package.grants), 'connections': hub.resolve(who, package.manifest)})
            return {'agents': packages}
        return invoke(req, get)

    @api.post('/refresh')
    def refresh(req: Request):
        return invoke(req, lambda who: hub.refresh(who), mutate=True)

    @api.get('/project')
    def project(req: Request):
        return invoke(req, lambda who: {'project': hub.project_state(who)})

    @api.post('/project')
    def link_project(body: ProjectLink, req: Request):
        if body.mode == 'managed':
            return invoke(req, lambda who: hub.use_managed_project(who), mutate=True)
        return invoke(req, lambda who: hub.link_project(who, body.api_key.get_secret_value(), body.composio_user_id), mutate=True)

    @api.delete('/project')
    def unlink_project(req: Request):
        return invoke(req, lambda who: hub.unlink_project(who), mutate=True)

    @api.post('/project/bind')
    def bind_project(body: ProjectBinding, req: Request):
        return invoke(req, lambda who: hub.bind_project(who, body.account_id, service=body.service, replace=body.replace, resource=body.resource), mutate=True)

    @api.post('/project/connect')
    def connect_project(body: Authorization, req: Request):
        return invoke(req, lambda who: body.connect(hub, who), mutate=True)

    @api.get('/catalogue')
    def catalogue(req: Request, q: str = '', cursor: str = '', limit: int = 48):
        return invoke(req, lambda who: hub.catalogue(q, who=who, cursor=cursor or None, limit=limit))

    @api.get('/apps/{service}')
    def app_detail(service: str, req: Request):
        return invoke(req, lambda who: hub.app_detail(who, service))

    @api.get('/apps/{service}/tools')
    def app_tools(service: str, req: Request, q: str = ''):
        return invoke(req, lambda who: hub.discover_tools(who, service, q)['result'])

    @api.post('/connect')
    def connect(body: Authorization, req: Request):
        return invoke(req, lambda who: body.connect(hub, who), mutate=True)

    @api.post('/authorize')
    def authorize(body: Authorization, req: Request):
        # Compatibility route: new OAuth always uses the same Platform path.
        return invoke(req, lambda who: body.connect(hub, who), mutate=True)

    @api.post('/custom')
    def custom(body: Custom, req: Request):
        return invoke(req, lambda who: hub.custom(who, body.model_dump()), mutate=True)

    @api.put('/custom/{ident}')
    def edit(ident: str, body: Custom, req: Request):
        return invoke(req, lambda who: hub.custom(who, body.model_dump(), ident), mutate=True)

    @api.post('/connections/{ident}/execute')
    def execute(ident: str, body: Execute, req: Request):
        # David and Jobs share this exact execution function and receipt store.
        import aos_jobs
        return invoke(req, lambda who: aos_jobs.connection_read(hub, who, ident, body.operation, body.arguments), mutate=True)

    @api.post('/connections/{ident}/verify')
    def verify(ident: str, body: Execute, req: Request):
        def read(who):
            if body.operation != 'tool' or not hub.row(who, ident)['config'].get('manual_verification'):
                raise ConnectionError('Select a read tool for the unverified account.')
            hub.execute(who, ident, 'tool', body.arguments, expected_mode='read')
            row = hub.row(who, ident)
            return {'message': hub._connection_message(row['connection_status'])}
        return invoke(req, read, mutate=True)

    @api.post('/connections/{ident}/david')
    def david_read(ident: str, body: Execute, req: Request):
        def read(who):
            # Same David implementation and command interface. This scoped
            # ingress lets customer sessions use their own reads without access
            # to legacy global operator conversation/Jobs surfaces.
            from aos_david import David
            instance = David(identity.path.parents[2], warm=False)
            try:
                return instance.turn('connections-' + who['workspace'][:70],
                                     '/connection ' + ident + ' ' + body.operation + ' ' + json.dumps(body.arguments),
                                     connection_context=(hub, who))
            finally:
                instance.close()
        return invoke(req, read, mutate=True)

    @api.post('/connections/{ident}/jobs')
    def job(ident: str, body: Execute, req: Request):
        def submit(who):
            import aos_jobs
            job = hub.submit_job(who, ident, body.operation, body.arguments)
            runner = get_runner() if get_runner else None
            if runner:
                runner.submit(job['id'])
            else:
                # Isolated local acceptance uses the same durable claim/execute/receipt
                # path without starting production recovery or a second worker pool.
                job = aos_jobs.execute(identity.path.parents[2], job['id'])
            return {'job_id': job['id'], 'status': job['status'], 'result': job.get('result'), 'detail': job.get('detail'), 'message': 'Submitted through the existing TTROS Jobs store and executor.'}
        return invoke(req, submit, mutate=True)

    @api.post('/connections/{ident}/{action}')
    def change(ident: str, action: str, req: Request):
        return invoke(req, lambda who: hub.change(who, ident, action), mutate=True)

    @api.put('/connections/{ident}/access')
    def access(ident: str, body: Access, req: Request):
        return invoke(req, lambda who: hub.access(who, ident, body.mode, body.agents), mutate=True)

    @api.post('/agents/{agent_ref}/jobs')
    def agent_job(agent_ref: str, body: AgentTask, req: Request):
        def submit(who):
            import aos_jobs
            job = hub.submit_agent_job(who, agent_ref, body.input, body.permissions)
            runner = get_runner() if get_runner else None
            if runner:
                runner.submit(job['id'])
            else:
                job = aos_jobs.execute(identity.path.parents[2], job['id'])
            return {'job_id': job['id'], 'status': job['status'], 'result': job.get('result'), 'detail': job.get('detail')}
        return invoke(req, submit, mutate=True)

    return api

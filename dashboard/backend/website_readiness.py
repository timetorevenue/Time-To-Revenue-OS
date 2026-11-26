"""Website-specific extension. The backend supplies its root and existing runner.
Revisit on website preparation API changes. Last touched: 2026-11-16.
"""
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import aos_jobs
import aos_website
import aos_website_prepare as prepare
import aos_website_seo as seo


class PreparationInput(BaseModel):
    project: str = ''
    repository: str = ''
    client_scope: str = ''
    request: str = Field(default='', max_length=1000)
    branch: str = Field(default='', max_length=120)
    affected_paths: list[str] = Field(default_factory=list, max_length=40)
    source_references: list[str] = Field(default_factory=list, max_length=40)
    acceptance_checks: list[dict[str, str]] = Field(default_factory=list, max_length=40)
    allowed_actions: list[str] = Field(default_factory=lambda: list(prepare.ACTIONS))
    draft: bool = False
    seo_strategy: dict | None = None


def router(root: Callable[[], Path], runner: Callable):
    routes = APIRouter(prefix='/api/website/preparations')

    def guarded(call):
        try:
            return call()
        except (aos_website.WebsiteError, aos_jobs.JobError, OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @routes.get('')
    def listing():
        return guarded(lambda: {'preparations': prepare.list_orders(root())})

    @routes.post('/seo-plan')
    def seo_plan(body: dict):
        return guarded(lambda: seo.build_plan(body))

    @routes.post('')
    def create(body: PreparationInput):
        return guarded(lambda: prepare.prepare(root(), body.model_dump(exclude={'draft'}), draft=body.draft))

    @routes.get('/{identity}')
    def detail(identity: str):
        return guarded(lambda: prepare.view(root(), prepare.load(root(), identity)))

    @routes.post('/{identity}/queue')
    def queue(identity: str):
        def create():
            job = prepare.queue(root(), identity)
            active = runner()
            if active is not None and job['status'] == 'queued':
                active.submit(job['id'])
            return {'job': aos_website.job_view(root(), job)}
        return guarded(create)

    return routes

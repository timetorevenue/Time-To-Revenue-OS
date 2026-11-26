"""Test launch HTTP boundary. No publish/send endpoints; local operator access only."""
import os
from pathlib import Path

import ttros_site
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
import aos_product_launch as launch

class LaunchRequest(BaseModel):
    request: str = Field(min_length=10,max_length=4000)
class LeadRequest(BaseModel):
    name: str = Field(min_length=1,max_length=120)
    email: str = Field(max_length=254)
    consent: bool
    site: str = ttros_site.SITE_ID

def router(root):
    api=APIRouter(prefix='/api/website/studio/launch')
    def guard(req):
        origin=req.headers.get('origin','')
        if req.url.hostname not in ('127.0.0.1','localhost','testserver'):
            raise HTTPException(403,'Local operator host required')
        if req.client.host not in ('127.0.0.1','::1','testclient') or (origin and not origin.startswith(('http://127.0.0.1:','http://localhost:'))):
            raise HTTPException(403,'Local operator access required')
    @api.get('')
    def status(req:Request):
        guard(req);return {'offer':launch.read(root()),'model_calls':0}
    @api.post('')
    def create(body:LaunchRequest,req:Request):
        guard(req)
        try:
            # The same David.turn path used by Ask David; an injected refusal makes this
            # bounded launch endpoint incapable of falling through to a paid model.
            from aos_david import David
            import aos_jobs
            def no_model(*args, **kwargs):
                raise ValueError('Only the TEST ONLY workflow audit launch is supported here')
            david=David(root(),model=no_model,warm=False)
            try:
                reply=david.turn('dashboard-launch',body.request)
            finally:
                david.close()
            if not reply.get('job_id'): raise ValueError(reply.get('response','Test launch request required'))
            job=aos_jobs.load(root(),reply['job_id'])
            if job['status']!='done': raise ValueError(job.get('detail','Launch job failed'))
            return {'job':job,'offer':launch.read(root()),'david':reply}
        except ValueError as exc: raise HTTPException(400,str(exc))
    @api.post('/leads')
    def lead(body:LeadRequest,req:Request):
        guard(req)
        try:return launch.capture(root(),**body.model_dump())
        except ValueError as exc:raise HTTPException(400,str(exc))
    @api.post('/webhook')
    async def webhook(req:Request):
        # No operator origin requirement: provider authentication is the raw-body signature.
        if int(req.headers.get('content-length','0'))>262144:raise HTTPException(413)
        chunks=[]; size=0
        async for chunk in req.stream():
            size+=len(chunk)
            if size>262144:raise HTTPException(413)
            chunks.append(chunk)
        raw=b''.join(chunks)
        try:return launch.payment(root(),raw,req.headers.get('stripe-signature',''))
        except ValueError as exc:raise HTTPException(400,str(exc))
    @api.post('/checkout')
    def checkout(req:Request):
        guard(req)
        try:return {'offer':launch.reconcile_checkout(root())}
        except ValueError as exc:raise HTTPException(400,str(exc))
    @api.post('/{action}')
    def blocked(action:str,req:Request):
        raise HTTPException(403,'Publishing, sending and live payments are not authorized by a test launch')
    return api

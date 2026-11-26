"""David's bounded test launch using aos_jobs, CRM and Website Studio. No sends/publication."""
from pathlib import Path
import fcntl
import hashlib
import json
import re
from contextlib import contextmanager
import aos_jobs
import aos_crm
from aos_queue_storage import durable_replace_text
from aos_stripe_test import StripeTest, StripeBlocked
from aos_authority import action_authorization, launch_request_problem

SITE = 'default-site'
OFFER = 'offer:test-workflow-audit'
PAGE = 'test-offer'

def path(root): return Path(root)/'results/product-launch/test-workflow-audit.json'

def read(root):
    p=path(root)
    return json.loads(p.read_text()) if p.exists() else None

@contextmanager
def lock(root):
    p=Path(root)/'queue/crm/product-launch.lock'; p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a') as h:
        fcntl.flock(h,fcntl.LOCK_EX)
        yield

def write(root, data): durable_replace_text(path(root),json.dumps(data,indent=2)+'\n')

def submit(root, request, conversation='dashboard-launch'):
    problem = launch_request_problem(request)
    if problem: raise ValueError(problem)
    job,_=aos_jobs.create(root,conversation_id=conversation,topic='product-launch',request=request,
        executor={'kind':'product_launch','action':'draft','site':SITE},dedupe_key='test-workflow-audit:'+conversation,
        restrictions=['no_brain_writes','no_connector_writes'])
    return aos_jobs.execute(root,job['id'])

def run_job(root,job):
    if job['executor'].get('site')!=SITE: raise ValueError('Wrong site')
    if job['executor']['action']=='fulfill':
        offer=read(root)
        if not offer: raise ValueError('Unknown fulfillment offer')
        body={'deliverable':offer['deliverable'],'launch_job':offer['job_id'],'offer_id':OFFER,'session':job['executor']['session'],'delivery':'Local deliverable draft prepared; no external delivery','test_only':True}
        dest=Path(root)/'results/agents'/aos_jobs.storage_key(job['id'])/'fulfillment.json'
        durable_replace_text(dest,json.dumps(body,indent=2)+'\n')
        return result(root,dest,'Test fulfillment prepared for review')
    decision = action_authorization(root, job, action='PRODUCT_LAUNCH_DRAFT', target=SITE)
    if not decision['authorized']:
        return {'status':'refused','failure_class':decision['basis'],'detail':decision['detail']}
    with lock(root):
        offer=read(root)
        if not offer:
            offer={'id':OFFER,'site':SITE,'test_only':True,'name':'TEST Workflow Audit','amount':4900,'currency':'cad','price_basis':'Suggested CAD 49 pilot price; owner review required',
              'job_id':job['id'],'status':'draft_review','specification':'A guided workflow audit workbook for a small business. Test package; no readiness or revenue claims.',
              'deliverable':'# Workflow Audit Workbook — TEST DRAFT\n1. Choose one recurring workflow.\n2. List trigger, owner, inputs and outputs.\n3. Time five runs and record delays.\n4. Rank three bottlenecks by hours lost.\n5. Design a seven-day improvement experiment with an owner and success metric.\n6. Review privacy, access and failure recovery before automation.',
              'marketing':{'email':[{'subject':'Map one workflow','body':'Choose one repeatable task. Write down its trigger, owner and outcome. Reply with the step where work waits.'},{'subject':'Find the costly handoff','body':'Measure five runs. Which handoff waits longest? Record the delay before choosing software.'},{'subject':'Try a seven-day improvement','body':'Choose one change and a measurable result. Review the test workflow audit workbook before buying. Unsubscribe requests must be honored.'}],
              'linkedin':'Before automating a workflow, measure where it waits. Our TEST workflow audit workbook maps ownership, delays and a seven-day improvement experiment. Draft for review.',
              'instagram':'One workflow. Five observations. One improvement experiment. Start with the handoff that waits longest. TEST workbook draft. #WorkflowImprovement'},'stripe':{'provider_verified':False,'blocker':'Test credentials not configured'},'payments':[],'leads':[],'publication':'blocked','email_send':'blocked','token_usage':{'model_calls':0,'total_tokens':0}}
            write(root,offer)
        aos_crm.record_event(root,client_scope='global',kind='offer.create',record_id=OFFER,payload={'name':offer['name'],'site':SITE,'test_only':True},request_key='launch-offer-v1')
        import website_studio_edits as edits
        from website_studio_sources import Sources
        store=edits.Drafts(Sources())
        if not offer.get('studio'):
            base=store.base(SITE,PAGE)
            intro=base['content'][0]['props']; fields=[k for k in intro if k.startswith('text')]
            if fields: intro[fields[0]]=offer['name']
            if len(fields)>1: intro[fields[1]]='Find the handoff that waits longest.'
            if len(fields)>2: intro[fields[2]]='Map one workflow, identify bottlenecks and plan a seven-day improvement. TEST ONLY workbook draft, proposed CAD 49.'
            saved=store.save(SITE,base,store.latest(SITE,PAGE),'david-launch',note='TEST offer landing draft',page=PAGE)
            offer['studio']={'page':PAGE,'version':saved['version'],'source':saved['revision'],'route':'/offers/test-workflow-audit'}
        decision = action_authorization(root, job, action='STRIPE_TEST_CHECKOUT', target=SITE)
        try:
            if not offer['stripe'].get('provider_verified'):
                if decision['authorized']:
                    offer['stripe']=StripeTest().checkout(offer,checkpoint=lambda value:write(root,value))
                else:
                    offer['stripe']={'provider_verified':False,'blocker':decision['detail'],'authorization_basis':decision['basis']}
        except StripeBlocked as exc: offer['stripe']={'provider_verified':False,'blocker':str(exc)}
        write(root,offer)
        snapshot=Path(root)/'results/agents'/aos_jobs.storage_key(job['id'])/'launch-package.json'
        durable_replace_text(snapshot,json.dumps(offer,indent=2)+'\n')
        return result(root,snapshot,'Test launch draft ready for review; '+('Stripe test checkout verified' if offer['stripe'].get('provider_verified') else offer['stripe'].get('blocker','Stripe checkout blocked')))

def result(root,dest,summary):
    return {'status':'ok','output':{'summary':summary},'artifacts':[{'path':str(dest.relative_to(root)),'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}],'usage':{'available':True,'model_calls':0,'input_tokens':0,'output_tokens':0,'cached_input_tokens':0,'total_tokens':0,'exact':True},'effects':[]}

def capture(root,*,name,email,consent,site=SITE):
    if site!=SITE or not isinstance(consent,bool): raise ValueError('Wrong site or missing explicit consent choice')
    if not name or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',email): raise ValueError('Name and valid email required')
    with lock(root):
        offer=read(root)
        if not offer: raise ValueError('Create offer first')
        email=email.strip().casefold()
        state=aos_crm.project(root,scope='global')
        contact=next((row for row in state['contacts'].values() if row.get('email')==email),None)
        cid=contact['id'] if contact else 'contact:launch-'+hashlib.sha256(email.encode()).hexdigest()[:20]
        source='https://www.core-site.example/offers/test-workflow-audit'
        if not contact:
            payload={'name':name,'email':email,'consent':{'guide_access':True,'follow_up':consent,'notice_version':'launch-test-v1'},'attribution':{'offer':OFFER,'source_page':'/offers/test-workflow-audit'}}
            aos_crm.record_event(root,client_scope='global',kind='contact.create',record_id=cid,payload=payload,source_refs=[source],request_key='launch-contact-'+cid)
        elif not consent and (contact.get('consent') or {}).get('follow_up'):
            aos_crm.record_event(root,client_scope='global',kind='contact.consent',record_id=cid,payload={'follow_up':False,'notice_version':'launch-test-v1'},source_refs=[source],request_key='launch-revoke-'+cid)
        contact=aos_crm.record_detail(root,cid,scope='global')
        subscription=contact.get('subscription') or {}
        allowed=bool((contact.get('consent') or {}).get('follow_up') and not subscription.get('suppressed') and not subscription.get('delivery_review_required') and not contact.get('do_not_contact'))
        if cid not in offer['leads']: offer['leads'].append(cid)
        write(root,offer)
        return {'contact':cid,'nurture_allowed':allowed,'email_send':'blocked'}

def payment(root,raw,signature,provider=None):
    provider=provider or StripeTest()
    event=provider.verify(raw,signature)
    with lock(root):
        offer=read(root)
        if not offer: raise ValueError('Unknown offer')
        if not isinstance(event,dict) or not isinstance(event.get('data'),dict) or not isinstance(event['data'].get('object'),dict):
            raise ValueError('Malformed payment event')
        obj=event['data']['object']; meta=obj.get('metadata',{})
        if not isinstance(meta,dict) or obj.get('livemode') is True: raise ValueError('Invalid metadata or live object')
        if meta.get('site')!=SITE or meta.get('offer_id')!=OFFER or meta.get('test_only')!='true': raise ValueError('Wrong site or offer')
        if offer['stripe'].get('account')!=provider.account or obj.get('payment_link')!=offer['stripe'].get('link'): raise ValueError('Wrong account or payment link')
        eid=event.get('id',''); sid=obj.get('id','')
        if not re.fullmatch(r'evt_[A-Za-z0-9_]+',eid) or not re.fullmatch(r'cs_test_[A-Za-z0-9_]+',sid): raise ValueError('Invalid event/session identity')
        digest=hashlib.sha256(raw).hexdigest()
        prior=next((x for x in offer['payments'] if x['event']==eid),None)
        if prior:
            if prior['digest']!=digest: raise ValueError('Reused event identity with different payload')
            return prior
        paid=event.get('type') in ('checkout.session.completed','checkout.session.async_payment_succeeded') and obj.get('payment_status')=='paid'
        if paid and (obj.get('amount_total')!=offer['amount'] or obj.get('currency')!=offer['currency']): raise ValueError('Wrong amount/currency')
        receipt={'event':eid,'session':sid,'digest':digest,'status':'paid' if paid else 'not_paid','provider_verified':bool(offer['stripe'].get('provider_verified'))}
        if paid:
            job,_=aos_jobs.create(root,conversation_id='launch-fulfillment',topic='product-launch',request='Prepare TEST local fulfillment '+sid,
              executor={'kind':'product_launch','action':'fulfill','site':SITE,'session':sid},dedupe_key='launch-fulfill:'+provider.account+':'+sid)
            job=aos_jobs.execute(root,job['id']);receipt['fulfillment_job']=job['id']
            if job['status']!='done': raise ValueError('Fulfillment needs retry')
            aos_crm.record_event(root,client_scope='global',kind='purchase.create',record_id='purchase:'+sid,payload={'name':'TEST purchase','site':SITE,'test_only':True,'offer_id':OFFER,'session_id':sid,'account':provider.account,'fulfillment_job':job['id']},request_key='launch-purchase-'+sid)
        offer['payments'].append(receipt);write(root,offer);return receipt


def reconcile_checkout(root):
    with lock(root):
        offer = read(root)
        if not offer: raise ValueError('Create the test offer first')
        if not offer['stripe'].get('provider_verified'):
            job = aos_jobs.load(root, offer['job_id'])
            decision = action_authorization(root, job, action='STRIPE_TEST_CHECKOUT', target=SITE)
            if not decision['authorized']: raise ValueError(decision['detail'])
            offer['stripe'] = StripeTest().checkout(offer, checkpoint=lambda value: write(root, value))
            write(root, offer)
        return offer

import hashlib,hmac,json,time,copy
from pathlib import Path
import pytest
import aos_product_launch as launch
import aos_crm,aos_jobs
from aos_stripe_test import StripeTest,StripeBlocked

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('TTROS_STUDIO_STATE',str(tmp_path/'studio'))
    monkeypatch.delenv('TTROS_STRIPE_TEST_KEY',raising=False)
    job=launch.submit(tmp_path,'Create a TEST ONLY workflow audit offer','test-launch')
    assert job['status']=='done',job
    return tmp_path,job

def provider():
    return StripeTest(account='acct_fixture',key='rk_test_fixture',webhook_secret='whsec_fixture')

def event(kind='checkout.session.completed',status='paid',eid='evt_fixture',sid='cs_test_fixture'):
    return {'id':eid,'livemode':False,'account':'acct_fixture','type':kind,'data':{'object':{'id':sid,'payment_status':status,'payment_link':'plink_fixture','amount_total':4900,'currency':'cad','metadata':{'site':launch.SITE,'offer_id':launch.OFFER,'test_only':'true'}}}}

def signed(e):
    raw=json.dumps(e).encode();stamp=str(int(time.time()));sig=hmac.new(b'whsec_fixture',stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest();return raw,'t='+stamp+',v1='+sig

def bind(root):
    offer=launch.read(root);offer['stripe']={'account':'acct_fixture','link':'plink_fixture','provider_verified':False,'simulation':True};launch.write(root,offer)

def test_launch_and_replays(setup):
    root,job=setup
    assert launch.submit(root,job['request'],'test-launch')['id']==job['id']
    assert not launch.read(root)['stripe']['provider_verified']
    bind(root)
    assert not launch.capture(root,name='Fixture',email='fixture@example.invalid',consent=False)['nurture_allowed']
    raw,sig=signed(event());first=launch.payment(root,raw,sig,provider())
    assert launch.payment(root,raw,sig,provider())==first
    second=launch.payment(root,*signed(event(eid='evt_second')),provider())
    assert second['fulfillment_job']==first['fulfillment_job']
    state=aos_crm.project(root)
    assert len(state['purchases'])==1 and len(state['offers'])==1 and len(state['contacts'])==1
    assert len(aos_jobs.list_jobs(root))==2
    assert aos_jobs.load(root,first['fulfillment_job'])['status']=='done'
    assert not first['provider_verified']

@pytest.mark.parametrize('change',[{'livemode':True},{'account':'acct_wrong'}])
def test_bad_account(setup,change):
    root,_=setup;bind(root);e=event();e.update(change)
    with pytest.raises(ValueError):launch.payment(root,*signed(e),provider())

def test_wrong_site_amount_signature_and_failure(setup):
    root,_=setup;bind(root)
    for field,value in [('site','other'),('offer_id','offer:other')]:
        e=event();e['data']['object']['metadata'][field]=value
        with pytest.raises(ValueError):launch.payment(root,*signed(e),provider())
    e=event();e['data']['object']['amount_total']=1
    with pytest.raises(ValueError):launch.payment(root,*signed(e),provider())
    raw,sig=signed(event())
    with pytest.raises(ValueError):launch.payment(root,raw+b' ',sig,provider())
    with pytest.raises(ValueError):provider().verify(raw,sig,now=time.time()+301)
    receipt=launch.payment(root,*signed(event('checkout.session.async_payment_failed','unpaid')),provider())
    assert receipt['status']=='not_paid' and 'fulfillment_job' not in receipt
    with pytest.raises(ValueError):launch.capture(root,name='x',email='x@example.invalid',consent=None)
    with pytest.raises(ValueError):launch.capture(root,name='x',email='x@example.invalid',consent=True,site='wrong')

def test_provider_adapter():
    calls=[]
    def transport(method,path,fields,idem):
        calls.append((path,idem))
        if path=='account':return {'id':'acct_fixture'}
        return {'id':{'products':'prod_fixture','prices':'price_fixture','payment_links':'plink_fixture'}[path],'livemode':False,'url':'https://buy.stripe.com/test_fixture'}
    p=StripeTest(account='acct_fixture',key='rk_test_fixture',transport=transport)
    offer={'id':launch.OFFER,'name':'TEST audit','amount':4900,'currency':'cad'}
    assert p.checkout(offer)==p.checkout(offer)
    assert calls[:4]==calls[4:]
    with pytest.raises(StripeBlocked):StripeTest(account='acct_fixture',key='sk_live_forbidden')
    with pytest.raises(StripeBlocked):StripeTest(site='wrong',account='acct_fixture',key='sk_test_fixture')

def test_http_and_david_boundaries(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from product_launch import router
    from aos_david import David
    root,job=setup
    app=FastAPI();app.include_router(router(lambda:root));client=TestClient(app)
    assert client.post('/api/website/studio/launch/publish').status_code==403
    assert client.post('/api/website/studio/launch/send').status_code==403
    assert client.get('/api/website/studio/launch',headers={'Origin':'https://attacker.invalid'}).status_code==403
    assert client.post('/api/website/studio/launch/leads',json={'name':'x','email':'x@example.invalid'}).status_code==422
    assert client.post('/api/website/studio/launch/webhook',content=b'{}').status_code==400
    david=David(root,model=lambda *a,**k:pytest.fail('No model charge allowed'),warm=False)
    reply=david.turn('test-launch',job['request'])
    assert reply['job_id']==job['id']
    david.close()

def test_provider_checkpoint_and_uncertain_retry():
    calls=[]
    def transport(method,path,fields,idem):
        calls.append(path)
        if path=='account':return {'id':'acct_fixture'}
        return {'id':{'products':'prod_fixture','prices':'price_fixture','payment_links':'plink_fixture'}[path],'livemode':False,'url':'https://buy.stripe.com/test_fixture'}
    p=StripeTest(account='acct_fixture',key='rk_test_fixture',transport=transport)
    offer={'id':launch.OFFER,'name':'TEST audit','amount':4900,'currency':'cad'}
    saved=[];p.checkout(offer,checkpoint=lambda x:saved.append(copy.deepcopy(x)))
    p.checkout(offer,checkpoint=lambda x:saved.append(copy.deepcopy(x)))
    assert calls==['account','products','prices','payment_links','account']
    journal=offer['stripe_requests']['products'];journal.pop('result');journal['started']=time.time()-24*3600
    with pytest.raises(StripeBlocked):p.checkout(offer,checkpoint=lambda x:None)

def test_launch_reuses_contact_and_honors_suppression(setup):
    import aos_subscribers
    root,_=setup
    aos_crm.record_event(root,client_scope='global',kind='contact.create',record_id='contact:existing',payload={'name':'Existing','email':'known@example.invalid','consent':{'guide_access':True,'follow_up':True,'notice_version':'launch-test-v1'}},request_key='existing-contact')
    first=launch.capture(root,name='Existing',email='KNOWN@example.invalid',consent=True)
    assert first['contact']=='contact:existing' and first['nurture_allowed']
    aos_subscribers._event(root,'contact:existing',{'action':'suppressed','reason':'unsubscribe','message_id':'','provider_event_id':'local-unsubscribe'},'local-suppression','global')
    assert not launch.capture(root,name='Existing',email='known@example.invalid',consent=True)['nurture_allowed']
    assert len(aos_crm.project(root)['contacts'])==1
    assert not launch.capture(root,name='Existing',email='known@example.invalid',consent=False)['nurture_allowed']
    assert not launch.capture(root,name='Existing',email='known@example.invalid',consent=True)['nurture_allowed']
    assert not aos_crm.project(root)['opportunities']

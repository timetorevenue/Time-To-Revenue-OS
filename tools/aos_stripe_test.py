"""Narrow Stripe TEST adapter. No connection management, live keys or live objects.
Secrets are injected by the runtime; never returned or logged. No .env loading.
"""
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

class StripeBlocked(ValueError):
    pass

class StripeTest:
    def __init__(self, *, site='default-site', account=None, key=None, webhook_secret=None, transport=None):
        if site != 'default-site':
            raise StripeBlocked('Wrong site')
        self.account = account or os.environ.get('TTROS_STRIPE_TEST_ACCOUNT', '')
        self.key = key if key is not None else os.environ.get('TTROS_STRIPE_TEST_KEY', '')
        self.secret = webhook_secret if webhook_secret is not None else os.environ.get('TTROS_STRIPE_TEST_WEBHOOK_SECRET', '')
        if not self.key.startswith(('sk_test_', 'rk_test_')) or not self.account.startswith('acct_'):
            raise StripeBlocked('Provision TTROS_STRIPE_TEST_KEY (restricted test key), TTROS_STRIPE_TEST_ACCOUNT and TTROS_STRIPE_TEST_WEBHOOK_SECRET through runtime secret injection. Live keys refused.')
        self.transport = transport or self._http

    def connection_status(self):
        """Authenticated read only; injected TEST key never leaves this adapter."""
        account = self.transport('GET', 'account', {}, '')
        if account.get('id') != self.account:
            raise StripeBlocked('Stripe account mismatch')
        return {'account': self.account, 'mode': 'test', 'authenticated_read': True,
                'webhook_configured': self.secret.startswith('whsec_'),
                'product_checkout_webhook_verified': False}

    def _http(self, method, path, fields, idem):
        headers = {'Authorization': 'Bearer '+self.key, 'Stripe-Version': '2024-06-20'}
        if idem: headers['Idempotency-Key'] = idem
        req = urllib.request.Request('https://api.stripe.com/v1/'+path, data=urllib.parse.urlencode(fields).encode() if method=='POST' else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as res: return json.load(res)
        except Exception:
            raise StripeBlocked('Stripe test request failed; inspect provider status securely') from None

    def checkout(self, offer, checkpoint=None):
        acct = self.transport('GET', 'account', {}, '')
        if acct.get('id') != self.account: raise StripeBlocked('Stripe account mismatch')
        scope = {'metadata[site]':'default-site', 'metadata[offer_id]':offer['id'], 'metadata[test_only]':'true'}
        journal = offer.setdefault('stripe_requests', {})
        def post(path, fields):
            idem = hashlib.sha256(json.dumps([self.account, offer['id'], path, fields], sort_keys=True).encode()).hexdigest()
            previous = journal.get(path)
            if previous and previous['key'] != idem:
                raise StripeBlocked('Stripe request mapping changed; reconcile before retry')
            if previous and previous.get('result'):
                return previous['result']
            if previous and time.time()-previous['started']>23*3600:
                raise StripeBlocked('Uncertain Stripe request older than 23h; reconcile provider objects before retry')
            if checkpoint:
                journal[path] = previous or {'key':idem,'started':time.time()}
                checkpoint(offer)
            result = self.transport('POST', path, fields, idem)
            if result.get('livemode') is not False: raise StripeBlocked('Provider returned a live or unverified object')
            result = {k:result[k] for k in ('id','url','livemode') if k in result}
            if checkpoint:
                journal[path]['result'] = result
                checkpoint(offer)
            return result
        product = post('products', {'name':offer['name'], **scope})
        price = post('prices', {'product':product['id'], 'unit_amount':offer['amount'], 'currency':offer['currency'], **scope})
        link = post('payment_links', {'line_items[0][price]':price['id'], 'line_items[0][quantity]':1, **scope})
        if not link.get('url','').startswith('https://buy.stripe.com/test_'): raise StripeBlocked('Non-test checkout URL refused')
        return {'account': self.account, 'product':product['id'], 'price':price['id'], 'link':link['id'], 'url':link['url'], 'provider_verified':True}

    def verify(self, raw, signature, *, now=None):
        if len(raw)>262144 or not self.secret.startswith('whsec_'): raise StripeBlocked('Webhook secret unavailable or payload too large')
        try:
            parts = [p.split('=',1) for p in signature.split(',')]
            stamps = [v for k,v in parts if k=='t']
            if len(stamps)!=1: raise ValueError()
            stamp = stamps[0]
            if abs((time.time() if now is None else now)-int(stamp))>300: raise ValueError()
            expected = hmac.new(self.secret.encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
            if not any(hmac.compare_digest(expected,v) for k,v in parts if k=='v1'): raise ValueError()
            event = json.loads(raw)
            if not isinstance(event, dict): raise ValueError()
            if event.get('livemode') is not False or (event.get('account') and event['account']!=self.account): raise ValueError()
            return event
        except (ValueError, TypeError, KeyError):
            raise StripeBlocked('Invalid, expired, live or wrong-account webhook') from None

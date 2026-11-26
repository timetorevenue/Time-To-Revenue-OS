"""Offline HTTP acceptance fixture only. Never used by production startup."""
from pathlib import Path
import os
os.environ['TTROS_STUDIO_OFFLINE']='1'
from aos_stripe_test import StripeTest
import aos_product_launch

class FixtureStripe(StripeTest):
    def __init__(self):
        def transport(method,path,fields,idem):
            if path=='account':return {'id':'acct_fixture'}
            return {'id':{'products':'prod_fixture','prices':'price_fixture','payment_links':'plink_fixture'}[path], 'livemode':False,'url':'https://buy.stripe.com/test_fixture'}
        super().__init__(account='acct_fixture',key='rk_test_fixture',webhook_secret='whsec_fixture',transport=transport)
    def checkout(self,offer,checkpoint=None):
        data=super().checkout(offer,checkpoint=checkpoint)
        data.update(provider_verified=False,simulation=True,blocker='Offline fixture transport; no real Stripe provider request')
        return data

aos_product_launch.StripeTest=FixtureStripe
from studio_local import app

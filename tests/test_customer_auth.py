"""Signed-token verification and customer orders across separate conversations."""
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt

import api
import authentication
import database
import history
import sessions
from test_kiosk import ScriptedModel
import agent

HEADER = 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id'


class CustomerAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.owners = {}
        redis = MagicMock()
        def save(key, value, nx=False):
            if nx and key in self.owners:
                return False
            self.owners[key] = value
            return True
        redis.set.side_effect = save
        redis.get.side_effect = self.owners.get
        keys = MagicMock()
        keys.get_signing_key_from_jwt.return_value = SimpleNamespace(key=self.key.public_key())
        for p in [patch.dict(os.environ, {'KIOSK_AUTH_MODE': 'cognito', 'COGNITO_REGION': 'eu-north-1',
                'COGNITO_USER_POOL_ID': 'pool', 'COGNITO_CLIENT_ID': 'client'}),
                patch.object(authentication, 'signing_keys', return_value=keys),
                patch.object(sessions, 'get_redis', return_value=redis),
                patch.object(history, 'get_redis', return_value=None),
                patch.object(database, 'DB_PATH', Path(self.temp.name) / 'orders.sqlite3')]:
            p.start()
            self.addCleanup(p.stop)
        history.local_history.clear()
        history.local_locks.clear()
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)

    def token(self, customer='alice', **changes):
        claims = {'sub': customer, 'iss': 'https://cognito-idp.eu-north-1.amazonaws.com/pool',
                  'client_id': 'client', 'token_use': 'access', 'iat': int(time.time()),
                  'exp': int(time.time()) + 600}
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm='RS256', headers={'kid': 'test'})

    def test_same_customer_sees_order_in_another_conversation(self):
        with patch.object(agent, 'get_model', return_value=ScriptedModel([
                {'product_id': 'cold_coffee', 'quantity': 1}])):
            result = self.client.post('/invocations', headers={HEADER: 'chat-one'},
                json={'prompt': 'Save coffee', 'access_token': self.token()})
        self.assertEqual(result.status_code, 200, result.text)
        response = self.client.get('/order', headers={HEADER: 'chat-two',
            'Authorization': 'Bearer ' + self.token()})
        self.assertEqual(response.json()['total_paise'], 12000)
        self.assertEqual(database.get_order('alice')['total_paise'], 12000)
        self.assertEqual(database.get_order('chat-one')['items'], [])
        self.assertEqual(history.load_history('chat-two'), [])

    def test_another_customer_cannot_open_existing_session(self):
        self.client.get('/order', headers={HEADER: 'chat-one', 'Authorization': 'Bearer ' + self.token()})
        result = self.client.get('/order', headers={HEADER: 'chat-one',
            'Authorization': 'Bearer ' + self.token('bob')})
        self.assertEqual(result.status_code, 403)

    def test_missing_expired_wrong_issuer_and_wrong_client_denied(self):
        tokens = [None, self.token(exp=int(time.time()) - 1), self.token(client_id='other'),
                  self.token(iss='https://wrong.example'), self.token(token_use='id')]
        for token in tokens:
            result = self.client.post('/invocations', headers={HEADER: 'chat-one'},
                json={'prompt': 'Hi', 'access_token': token})
            self.assertEqual(result.status_code, 401, result.text)
        self.assertFalse(self.owners)

    def test_forged_signature_rejected(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = jwt.decode(self.token(), options={'verify_signature': False})
        token = jwt.encode(claims, other_key, algorithm='RS256')
        with self.assertRaises(Exception) as error:
            authentication.verify_login(token)
        self.assertEqual(error.exception.status_code, 401)

    def test_reset_clears_customers_draft_not_other_customers(self):
        for customer in ['alice', 'bob']:
            database.save_order(customer, [{'product_id': 'cold_coffee', 'quantity': 1}])
        result = self.client.post('/reset', headers={HEADER: 'new-chat',
            'Authorization': 'Bearer ' + self.token()})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(database.get_order('alice')['items'], [])
        self.assertEqual(database.get_order('bob')['total_paise'], 12000)

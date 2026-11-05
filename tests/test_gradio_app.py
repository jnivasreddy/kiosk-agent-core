"""Customer isolation and Cognito boundaries; no real signup or paid calls."""
import importlib.util
import io
import json
import unittest
from unittest.mock import MagicMock, patch


@unittest.skipUnless(importlib.util.find_spec('gradio'), 'Install requirements-gradio.txt')
class GradioTests(unittest.TestCase):
    def setUp(self):
        import gradio_app
        self.app = gradio_app
        self.saved = {}
        self.redis = MagicMock()
        self.redis.get.side_effect = self.saved.get
        def save(key, value, **kwargs):
            if kwargs.get('nx') and key in self.saved:
                return False
            self.saved[key] = value
            return True
        self.redis.set.side_effect = save
        self.redis.delete.side_effect = lambda key: self.saved.pop(key, None)
        self.redis.lock.return_value.acquire.return_value = True
        self.cognito = MagicMock()
        self.agent = MagicMock()
        self.patchers = [patch.object(self.app, 'redis', self.redis),
                         patch.object(self.app, 'cognito', self.cognito),
                         patch.object(self.app, 'agentcore', self.agent)]
        for p in self.patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patchers)])

    def test_verified_customer_gets_server_login_not_tokens(self):
        self.cognito.initiate_auth.return_value = {'AuthenticationResult': {
            'AccessToken': 'private-token', 'ExpiresIn': 3600}}
        self.cognito.get_user.return_value = {'UserAttributes': [
            {'Name': 'sub', 'Value': 'customer-a'},
            {'Name': 'email_verified', 'Value': 'true'}]}
        login_id, session_id, status, password = self.app.sign_in('a@example.com', 'private-password', '')
        self.assertNotIn('private-token', login_id)
        self.assertEqual(password, '')
        self.assertEqual(self.app.get_customer(login_id), 'customer-a')
        self.cognito.get_user.assert_called_once_with(AccessToken='private-token')
        self.assertEqual(session_id, '')

    def test_anonymous_or_other_customer_cannot_invoke(self):
        self.saved['kiosk:web:login:a'] = json.dumps({'customer_id': 'customer-a', 'access_token': 'token-a'})
        self.saved['kiosk:web:login:b'] = json.dumps({'customer_id': 'customer-b', 'access_token': 'token-b'})
        sid, *_ = self.app.start_chat('a')
        for login in ('', 'b'):
            with self.assertRaises(self.app.gr.Error):
                self.app.send_message('Hi', login, sid, [])
        self.agent.invoke_agent_runtime.assert_not_called()

    def test_chat_reuses_owned_session_and_logout_revokes(self):
        self.saved['kiosk:web:login:a'] = json.dumps({'customer_id': 'customer-a', 'access_token': 'token-a'})
        sid, *_ = self.app.start_chat('a')
        self.agent.invoke_agent_runtime.return_value = {'response': io.BytesIO(json.dumps({
            'answer': 'Hello', 'order': {}, 'steps': []}).encode())}
        _, messages, _, _ = self.app.send_message('Hi', 'a', sid, [])
        args = self.agent.invoke_agent_runtime.call_args.kwargs
        self.assertEqual(args['runtimeSessionId'], sid)
        self.assertEqual(json.loads(args['payload']), {'prompt': 'Hi', 'access_token': 'token-a'})
        self.assertEqual(len(messages), 2)
        self.app.sign_out('a')
        with self.assertRaises(self.app.gr.Error):
            self.app.get_customer('a')

    def test_password_challenge_does_not_authenticate(self):
        self.cognito.initiate_auth.return_value = {'ChallengeName': 'NEW_PASSWORD_REQUIRED'}
        login, *_ = self.app.sign_in('a@example.com', 'password', '')
        self.assertEqual(login, '')
        self.assertEqual(self.saved, {})
        self.cognito.get_user.assert_not_called()

    def test_signup_and_confirmation_use_cognito(self):
        self.cognito.sign_up.return_value = {'UserConfirmed': False}
        self.assertIn('email', self.app.sign_up('a@example.com', 'password'))
        self.app.confirm_email('a@example.com', '123456')
        self.assertEqual(self.cognito.confirm_sign_up.call_args.kwargs['ConfirmationCode'], '123456')

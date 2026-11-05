"""Cognito customers → Redis sessions → AgentCore. Run: python gradio_app.py --share"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv
import gradio as gr
from redis import Redis
from uuid import uuid4

load_dotenv(Path(__file__).with_name('.env'))
REGION = os.getenv('COGNITO_REGION', 'eu-north-1')
CLIENT_ID = os.getenv('COGNITO_CLIENT_ID', '')
CLIENT_SECRET = os.getenv('COGNITO_CLIENT_SECRET', '')
RUNTIME_ARN = os.getenv('KIOSK_RUNTIME_ARN',
    'arn:aws:bedrock-agentcore:eu-north-1:463277661277:runtime/kiosk_class_demo-aA5WHrCAW9')
ENDPOINT = os.getenv('KIOSK_ENDPOINT', 'DEFAULT')


def clients():
    """Cognito customer APIs and AgentCore calls have separate authentication."""
    profile = os.getenv('AWS_PROFILE')
    session = boto3.Session(profile_name=profile, region_name=REGION)
    config = Config(connect_timeout=10, read_timeout=180, retries={'max_attempts': 0})
    return session.client('cognito-idp', config=config), session.client('bedrock-agentcore',
        region_name=RUNTIME_ARN.split(':')[3], config=config)


cognito = agentcore = redis = None


def secret_hash(email):
    """Cognito app-client proof. The client secret never goes to the browser."""
    digest = hmac.new(CLIENT_SECRET.encode(), (email + CLIENT_ID).encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def sign_up(email, password):
    try:
        args = dict(ClientId=CLIENT_ID, Username=email.strip(), Password=password,
                    UserAttributes=[{'Name': 'email', 'Value': email.strip()}])
        if CLIENT_SECRET:
            args['SecretHash'] = secret_hash(email.strip())
        result = cognito.sign_up(**args)
        return 'Account created. Sign in.' if result['UserConfirmed'] else 'Check your email for a code, then confirm below.'
    except ClientError as exc:
        return customer_error(exc)


def confirm_email(email, code):
    try:
        args = dict(ClientId=CLIENT_ID, Username=email.strip(), ConfirmationCode=code.strip())
        if CLIENT_SECRET:
            args['SecretHash'] = secret_hash(email.strip())
        cognito.confirm_sign_up(**args)
        return 'Email confirmed. Open Sign in and log in.'
    except ClientError as exc:
        return customer_error(exc)


def resend_code(email):
    try:
        args = dict(ClientId=CLIENT_ID, Username=email.strip())
        if CLIENT_SECRET:
            args['SecretHash'] = secret_hash(email.strip())
        cognito.resend_confirmation_code(**args)
        return 'A new confirmation code was sent.'
    except ClientError as exc:
        return customer_error(exc)


def customer_error(exc):
    code = exc.response['Error']['Code']
    return {
        'NotAuthorizedException': 'Sign-in failed. Check your email and password.',
        'UserNotFoundException': 'Sign-in failed. Check your email and password.',
        'UserNotConfirmedException': 'Confirm your email before signing in.',
        'UsernameExistsException': 'This account already exists. Try signing in.',
        'CodeMismatchException': 'That confirmation code is incorrect.',
        'ExpiredCodeException': 'The confirmation code expired. Request a new code.',
        'InvalidPasswordException': 'Choose a stronger password that meets the Cognito pool policy.',
        'InvalidParameterException': 'Check the inputs and enable USER_PASSWORD_AUTH in the Cognito app client.',
        'TooManyRequestsException': 'Too many attempts. Please wait and try again.',
        'LimitExceededException': 'Cognito limit reached. Please try later.',
    }.get(code, 'Cognito could not complete the request (' + code + ').')


def sign_in(email, password, previous_login):
    try:
        params = {'USERNAME': email.strip(), 'PASSWORD': password}
        if CLIENT_SECRET:
            params['SECRET_HASH'] = secret_hash(email.strip())
        response = cognito.initiate_auth(ClientId=CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH', AuthParameters=params)
        if 'AuthenticationResult' not in response:
            return '', '', 'Cognito requires an additional challenge. Complete account setup in Cognito first.', ''
        tokens = response['AuthenticationResult']
        # Cognito verifies the access token and returns the authenticated user.
        user = cognito.get_user(AccessToken=tokens['AccessToken'])
        attributes = {a['Name']: a['Value'] for a in user['UserAttributes']}
        if attributes.get('email_verified') != 'true':
            return '', '', 'Verify your email before chatting.', ''
        customer_id = attributes['sub']
        login_id = secrets.token_urlsafe(32)
        redis.set('kiosk:web:login:' + login_id, json.dumps({
            'customer_id': customer_id, 'access_token': tokens['AccessToken']}), ex=tokens['ExpiresIn'])
        if previous_login:
            redis.delete('kiosk:web:login:' + previous_login)
        # Browser gets no tokens. Redis retains the access token only until login expiry.
        return login_id, '', 'Signed in. Click Start new chat.', ''
    except ClientError as exc:
        return '', '', customer_error(exc), ''


def get_login(login_id):
    saved = redis.get('kiosk:web:login:' + login_id) if login_id else None
    if not saved:
        raise gr.Error('Sign in again. Your login may have expired or the app was updated.')
    try:
        login = json.loads(saved)
        if not login.get('customer_id') or not login.get('access_token'):
            raise ValueError('Incomplete login')
        return login
    except (ValueError, AttributeError):
        raise gr.Error('Sign in again to update your login session.') from None


def get_customer(login_id):
    return get_login(login_id)['customer_id']


def start_chat(login_id):
    customer_id = get_customer(login_id)
    session_id = str(uuid4())
    if not redis.set('kiosk:session:' + session_id, customer_id, nx=True):
        raise gr.Error('Please try starting a chat again.')
    return session_id, [], f'Customer: {customer_id}\nChat session: {session_id}', {}, []


def send_message(prompt, login_id, session_id, messages):
    login = get_login(login_id)
    customer_id = login['customer_id']
    if not session_id or redis.get('kiosk:session:' + session_id) != customer_id:
        raise gr.Error('Start a chat belonging to your current login first.')
    if not prompt.strip() or len(prompt) > 4000:
        raise gr.Error('Enter a message of 1–4000 characters.')
    # Keep concurrent requests from overwriting this browser conversation.
    lock = redis.lock('kiosk:web:lock:' + session_id, timeout=240)
    if not lock.acquire(blocking=False):
        raise gr.Error('Your previous message is still being processed.')
    try:
        response = agentcore.invoke_agent_runtime(agentRuntimeArn=RUNTIME_ARN,
            qualifier=ENDPOINT, runtimeSessionId=session_id, contentType='application/json',
            payload=json.dumps({'prompt': prompt, 'access_token': login['access_token']}).encode())
        body = response['response']
        try:
            result = json.loads(body.read())
        finally:
            body.close()
    except ClientError as exc:
        raise gr.Error('AgentCore request failed: ' + exc.response['Error']['Code']) from None
    finally:
        lock.release()
    messages = [*messages, {'role': 'user', 'content': prompt},
                {'role': 'assistant', 'content': result['answer']}]
    return '', messages, result.get('order', {}), result.get('steps', [])


def sign_out(login_id):
    if login_id:
        redis.delete('kiosk:web:login:' + login_id)
    return '', '', [], 'Signed out.', 'No active customer or chat.', {}, []


def build_app():
    with gr.Blocks(title='Corner Kiosk', theme=gr.themes.Soft(primary_hue='amber', secondary_hue='slate'),
                   css='.gradio-container {max-width: 1200px !important; margin: auto;}') as demo:
        # These State values live on the server, separately for each browser session.
        login = gr.State('')
        session = gr.State('')
        gr.Markdown('# ☕ Corner Kiosk\nSign up, confirm your email, and build your draft order.')
        with gr.Row():
            with gr.Column(scale=1, min_width=300):
                with gr.Tabs():
                    with gr.Tab('Sign in'):
                        email = gr.Textbox(label='Email', placeholder='you@example.com')
                        password = gr.Textbox(label='Password', type='password')
                        login_button = gr.Button('Sign in', variant='primary')
                    with gr.Tab('Create account'):
                        signup_email = gr.Textbox(label='Email')
                        signup_password = gr.Textbox(label='New password', type='password')
                        signup_button = gr.Button('Create account')
                        signup_status = gr.Markdown()
                        code = gr.Textbox(label='Email confirmation code')
                        confirm_button = gr.Button('Confirm email')
                        resend_button = gr.Button('Resend confirmation code')
                        gr.Markdown('Password policy is set by the Cognito user pool.')
                status = gr.Markdown('Not signed in.')
                new_chat = gr.Button('Start new chat', variant='primary')
                logout = gr.Button('Sign out')
            with gr.Column(scale=2):
                chat = gr.Chatbot(type='messages', label='Your conversation', height=420)
                prompt = gr.Textbox(label='Message', placeholder='Add one cold coffee and one veg sandwich')
                send = gr.Button('Send', variant='primary')
                with gr.Accordion('Your saved draft', open=True):
                    order = gr.JSON(label='Draft order')
        with gr.Accordion('Classroom view: what happens where?', open=False):
            gr.Markdown('**Cognito** verifies login → **this backend** creates a chat UUID and checks '
                '**Redis ownership** → **Boto3** invokes AgentCore → **agent tools** edit the draft.\n\n'
                'AgentCore verifies the Cognito access token, then binds order tools to its sub. '
                'New conversations use the same customer ID for orders. '
                'Orders in the runtime remain temporary. No payment is taken.')
            details = gr.Textbox(label='Verified identity and conversation', interactive=False, lines=2)
            steps = gr.JSON(label='Agent tool calls')
        signup_button.click(sign_up, [signup_email, signup_password], signup_status).then(
            lambda: '', outputs=signup_password)
        confirm_button.click(confirm_email, [signup_email, code], signup_status)
        resend_button.click(resend_code, signup_email, signup_status)
        login_button.click(sign_in, [email, password, login], [login, session, status, password]).then(
            lambda: ([], 'Start a new chat for this login.', {}, []), outputs=[chat, details, order, steps])
        new_chat.click(start_chat, login, [session, chat, details, order, steps])
        for event in [send.click, prompt.submit]:
            event(send_message, [prompt, login, session, chat], [prompt, chat, order, steps])
        logout.click(sign_out, login, [login, session, chat, status, details, order, steps])
    return demo


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--share', action='store_true', help='Create a temporary public Gradio link')
    args = parser.parse_args()
    if not CLIENT_ID or not os.getenv('REDIS_URL'):
        raise SystemExit('Set COGNITO_CLIENT_ID and REDIS_URL in .env first.')
    cognito, agentcore = clients()
    redis = Redis.from_url(os.environ['REDIS_URL'], decode_responses=True,
                          socket_connect_timeout=5, socket_timeout=5)
    redis.ping()
    build_app().queue(default_concurrency_limit=4).launch(
        server_name='127.0.0.1', share=args.share, show_error=False,
        blocked_paths=[str(Path(__file__).parent / '.env'), str(Path.home() / '.aws')],
    )

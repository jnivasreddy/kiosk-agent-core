# Share the kiosk with your class

The app has three small sections in gradio_app.py:

1. sign_up / confirm_email / sign_in call Cognito. get_user validates Cognito's
   issued access token and supplies the verified sub as customer_id. The access
   token is stored server-side in Redis until login expiry and forwarded to the
   runtime for independent signature/issuer/client/expiry verification.
2. get_customer / start_chat check the backend login and save session ownership
   in Redis. Login handles live in server-side gr.State, not browser inputs.
3. send_message checks ownership and invokes AgentCore through Boto3. AgentCore
   uses the same chat UUID for its Redis history. No model tool handles passwords.

## One-time Cognito setting

In your Cognito pool, open Applications → App clients → kiosk-customer-login.
Edit Authentication flows and enable **Sign in with username and password**
(`ALLOW_USER_PASSWORD_AUTH`). Keep other flows you already use. Save.

This app uses Cognito's customer API calls, not managed-login redirects. Your
existing notebook callback URL can stay unchanged; a Gradio share URL does not
need to be registered as a callback. Self-registration and email confirmation
must be enabled. MFA/new-password challenges are reported but not implemented in
this minimal classroom UI; use a self-registered confirmed customer for the demo.

## Local configuration

Use Python 3.12. In an isolated environment:

```bash
python -m pip install -r requirements-gradio.txt
```

Keep these in .env (never in Git):

```dotenv
COGNITO_REGION=eu-north-1
COGNITO_CLIENT_ID=your-client-id
COGNITO_CLIENT_SECRET=your-client-secret
REDIS_URL=your-provider-tls-url
AWS_PROFILE=kiosk
KIOSK_RUNTIME_ARN=your-runtime-arn-without-endpoint-suffix
KIOSK_ENDPOINT=DEFAULT
```

For an AWS-hosted backend using a role, omit AWS_PROFILE so Boto3 discovers its
role credentials. Environment access keys are also supported without a profile.
The IAM caller needs InvokeAgentRuntime permission on the runtime and endpoint.
Cognito customer sign-up/password sign-in APIs do not require adding Cognito
administrator permissions to that IAM user.

## Start and share

```bash
python gradio_app.py --share
```

Gradio prints the public HTTPS link. Keep the process and laptop running for the
class; this is a temporary tunnel, not permanent hosting. Customers open the link,
create an account, confirm their email, sign in and click Start new chat.
The password is submitted to this app's backend and then Cognito over HTTPS;
AWS keys and the Cognito client secret stay on the backend.

Use `python gradio_app.py` for local-only access. Sign out invalidates the server
login. Login expires with Cognito's token lifetime; sign in again when expired.
Redis owner records remain to prevent reassignment of an old chat; remove classroom
records intentionally after teaching rather than expiring ownership before history.

## Show students what happens

Expand “Classroom view” to see customer_id, session_id and returned tool calls.
Try two separate browser sessions with different customer accounts. Each must sign
in and gets a separate chat UUID. Sign out, then sending another message is denied.

The updated runtime verifies Cognito access tokens and uses sub as the SQLite
customer_id. Chat UUIDs only select message history and ownership. Within the same
database, a new conversation can read the same customer's draft.

Rebuild and redeploy the updated runtime ZIP before using this frontend. Set these
AgentCore environment variables: COGNITO_REGION, COGNITO_USER_POOL_ID,
COGNITO_CLIENT_ID, REDIS_URL and existing model configuration. The runtime does not
need the Cognito client secret. Cognito verification is enabled by default.

The old anonymous notebook needs KIOSK_AUTH_MODE=session_demo explicitly. That is
only a teaching compatibility mode: it disables customer authentication and uses
session-scoped orders. Leave this variable unset for authenticated customer use.

SQLite in AgentCore /tmp is still temporary and isolated per runtime session.
Consequently customer IDs are now correct, but cross-session cloud order sharing
requires replacing SQLite with shared durable storage. This update does not claim
cross-session persistence in AgentCore or migrate earlier session-keyed drafts.

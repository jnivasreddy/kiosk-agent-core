# Corner Kiosk: Streamlit + FastAPI

The UI is an HTTP client. The backend owns the agent, tools, history and orders.

```text
Streamlit (8501) → FastAPI (8080) → LangChain tool loop → SQLite
```

## Run locally

From this folder:

```bash
conda activate ai-dev
python -m pip install -r requirements.txt
```

Put `OPEN_ROUTER_API_KEY` in `.env`. Start two terminals with ai-dev active:

```bash
# Terminal 1: backend (one worker)
python -m uvicorn api:app --host 127.0.0.1 --port 8080
```

```bash
# Terminal 2: frontend
python -m streamlit run app.py
```

Open http://localhost:8501 for chat and http://localhost:8080/docs for the API.
If 8501 is occupied, use `--server.port 8502` on the Streamlit command.
If changing the backend port, set `KIOSK_API_URL` in `.env` to match.

## Files to teach

- `app.py`: calls the backend over HTTP; no agent or database imports.
- `api.py`: FastAPI routes, input validation and server-side history.
- `agent.py`: the same model → tool → model loop.
- `tools.py`: three kiosk tools.
- `database.py`: transactional SQLite order storage.
- `runtime.py`: optional launcher for that same FastAPI app on 0.0.0.0:8080.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | /ping | Health |
| GET | /menu | Public menu |
| GET | /order | Current session's saved draft |
| POST | /chat | Chat body: `{"prompt":"Save one coffee."}` |
| POST | /reset | Clear this session's draft and conversation |
| POST | /invocations | Same chat operation, for AgentCore HTTP compatibility |

Order, chat and reset require the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`
header. Streamlit supplies a random UUID and reuses it for the browser session.
This is session scoping, not a verified login. Keep the local teaching API on
loopback; public deployment requires authenticated session-to-user binding.

Try one coffee and one sandwich, then “Keep the sandwich and change the coffee
quantity to two”. Expand Tool calls and compare with the backend's saved draft.

Run checks with no paid model/AWS calls:

```bash
python -m unittest discover -s tests -v
```

SQLite is local and history is in one backend process. Use one Uvicorn worker;
restarts lose history. The UI keeps only display messages, not the agent's model
history. Use external state for scaled deployment. No checkout/payment is taken.

## AWS handoff

Nothing is deployed. Read HANDOFF.txt and AGENTCORE_SETUP.txt. The backend now
uses FastAPI instead of the former BedrockAgentCoreApp SDK server. The default
AgentCore invocation exposes chat via /invocations; it does not automatically
expose /menu, /order and /reset as public REST endpoints. The current Streamlit
client is local HTTP, not yet an authenticated AgentCore client.

## Redis conversation history

See [FLOW_CORRECTED.md](FLOW_CORRECTED.md) for the current request flow and planned Cognito login flow.
Set REDIS_URL in .env to store model history in Redis; without it, history uses
local process memory. api.py calls history.load_history and history.save_history.
Customer authentication and session-owner mapping are not implemented yet.
Include history.py when rebuilding the AgentCore deployment ZIP.

## Cognito + AgentCore classroom notebook

Install `requirements-notebook.txt` in the notebook kernel environment. Copy
`.env.example` to `.env` and fill the Cognito and Redis settings. Configure the
local AWS profile `kiosk` with permission to invoke your runtime. In
`cognito_agentcore_demo.ipynb`, update RUNTIME_ARN to your deployed runtime.
Run the notebook cells in order, then open http://localhost:8000. Cognito must
allow http://localhost:8000/auth/callback as the callback URL.

The browser demo validates Cognito login and stores customer/session ownership
in Redis before invoking AgentCore through Boto3. The deployed runtime still
uses chat-session IDs for orders; customer-bound cloud tools remain a follow-up.
See FLOW_CORRECTED.md for current and planned architecture. Notebooks are
committed without outputs. Secrets, databases and deployment ZIPs are excluded.

Build the Linux ARM64/Python 3.12 runtime ZIP with:

```bash
python scripts/build_runtime.py
```

Set REDIS_URL and the model credentials in AgentCore environment configuration.
Cognito notebook credentials belong to the website backend, not the runtime ZIP.

## Shareable Gradio customer app

See [GRADIO_SETUP.md](GRADIO_SETUP.md). Install requirements-gradio.txt using
Python 3.12, enable ALLOW_USER_PASSWORD_AUTH in your Cognito app client, and run
`python gradio_app.py --share`. The public link supports signup, email confirmation,
login and customer-scoped chat ownership. Keep the process running while sharing.
The classroom panel explains the identity/session/tool flow. No runtime redeploy
is required; the runtime still keys orders by conversation, as documented.

## Customer identity update

The updated API requires a Cognito access token in the invocation payload
`access_token` (or a Bearer header for local REST). authentication.py verifies it;
sessions.py binds session ownership in Redis; agent tools receive the verified
Cognito sub as customer_id. history.py still keys conversations by session_id.

Configure COGNITO_REGION, COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID and REDIS_URL in
AgentCore after rebuilding the ZIP. Existing unsigned classroom notebooks only
work with explicit KIOSK_AUTH_MODE=session_demo; that mode disables customer login.
Old session-keyed orders are not automatically assigned to customer identities.
SQLite remains temporary and per-runtime-session on AgentCore; a shared database
is still required for durable orders across cloud conversations.

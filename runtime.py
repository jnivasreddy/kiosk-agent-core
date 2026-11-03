"""Optional AgentCore-compatible launcher for the SAME FastAPI application."""
import os

os.environ.setdefault("KIOSK_DB_PATH", "/tmp/kiosk-agentcore.sqlite3")
from api import app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

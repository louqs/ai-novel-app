@echo off
cd /d D:\vibe coding\ai-novel-app
if not exist ".venv\Scripts\python.exe" (
    echo venv not found, creating...
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
)
echo Starting server at http://127.0.0.1:8080
echo Press Ctrl+C to stop
.venv\Scripts\python -m uvicorn web.backend.main:app --host 127.0.0.1 --port 8080
pause

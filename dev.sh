#!/bin/bash
# FastAPI development server using the official FastAPI CLI
#
# Usage: ./dev.sh
#
# This will start the FastAPI app with:
# - Auto-reload enabled
# - Listening on http://127.0.0.1:8000
# - API docs at http://127.0.0.1:8000/api/v2/docs
# - Environment variables loaded from .env file

echo "🚀 Starting FastAPI development server with FastAPI CLI..."
echo "📄 API docs: http://127.0.0.1:8000/api/v2/docs"
echo "🔍 Health check: http://127.0.0.1:8000/api/v2/healthz"
echo "⚠️  Press CTRL+C to stop the server"
echo ""

# Load environment variables and start FastAPI
exec fastapi dev api_v2/main.py --port 8000 --host 127.0.0.1
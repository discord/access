#!/bin/bash
# FastAPI production server using the official FastAPI CLI
#
# Usage: ./run.sh
#
# This will start the FastAPI app in production mode:
# - No auto-reload (for performance)
# - Listening on http://0.0.0.0:8000 (for containers/public access)
# - Environment variables loaded from .env file

echo "🚀 Starting FastAPI production server..."
echo "📄 API docs: http://localhost:8000/api/v2/docs"
echo "🔍 Health check: http://localhost:8000/api/v2/healthz"
echo "⚠️  Press CTRL+C to stop the server"
echo ""

# Load environment variables and start FastAPI in production mode
exec fastapi run api_v2/main.py --port 8000 --host 0.0.0.0
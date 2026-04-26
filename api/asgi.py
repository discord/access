"""ASGI entrypoint for production deployment.

Run via:
    uvicorn api.asgi:app --host 0.0.0.0 --port 3000
or under gunicorn with the uvicorn worker:
    gunicorn -k uvicorn.workers.UvicornWorker api.asgi:app
"""
from api.app import create_app

app = create_app()

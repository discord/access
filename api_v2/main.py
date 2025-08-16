"""
FastAPI application main entry point.
This runs alongside the existing Flask app during the migration.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_v2.middleware.security import SecurityHeadersMiddleware
from api_v2.routers import health, users, groups

app = FastAPI(
    title="Access Management API v2",
    description="FastAPI version of access management system",
    version="2.0.0",
    docs_url="/api/v2/docs",
    redoc_url="/api/v2/redoc",
    openapi_url="/api/v2/openapi.json"
)

# Add CORS middleware (for development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React dev server
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "baggage", "sentry-trace"],
)

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Include routers
app.include_router(health.router, prefix="/api/v2")
app.include_router(users.router, prefix="/api/v2")
app.include_router(groups.router, prefix="/api/v2")

@app.get("/")
async def root():
    """Root endpoint for FastAPI app"""
    return {"message": "Access Management API v2", "status": "running"}
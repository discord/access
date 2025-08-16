# FastAPI Usage Guide

This document explains how to use the FastAPI version of the Access Management API alongside the existing Flask app.

## Quick Start

### Development Server
```bash
./dev.sh
```
This starts the FastAPI development server with auto-reload enabled at http://127.0.0.1:8000

### Production Server
```bash
./run.sh
```
This starts the FastAPI production server (no auto-reload) at http://0.0.0.0:8000

### Manual FastAPI CLI Commands

**Development:**
```bash
fastapi dev api_v2/main.py --port 8000
```

**Production:**
```bash
fastapi run api_v2/main.py --port 8000
```

## Available Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Root endpoint |
| `GET /api/v2/healthz` | Basic health check |
| `GET /api/v2/healthz/db` | Database connectivity test |
| `GET /api/v2/healthz/auth` | Authentication test |
| `GET /api/v2/docs` | Interactive API documentation (Swagger UI) |
| `GET /api/v2/redoc` | Alternative API documentation (ReDoc) |

## Environment Configuration

The FastAPI app automatically loads configuration from the `.env` file:

```bash
# Required for development
FLASK_ENV=development
DATABASE_URI=sqlite:///access.db
CURRENT_OKTA_USER_EMAIL=your-email@example.com
CLIENT_ORIGIN_URL=http://localhost:3000

# Optional for production
OKTA_DOMAIN=your-okta-domain
OKTA_API_TOKEN=your-token
CLOUDFLARE_TEAM_DOMAIN=your-domain
```

## Running Alongside Flask

You can run both Flask and FastAPI simultaneously during the migration:

- **Flask app**: http://localhost:5000 (existing)
- **FastAPI app**: http://localhost:8000 (new)

Both apps share the same database and configuration.

## Testing

**Test the setup:**
```bash
python test_fastapi.py
```

**Test OpenAPI schema generation:**
```bash
python test_openapi.py
```

## Docker

**Build FastAPI container:**
```bash
docker build -f Dockerfile.fastapi -t access-fastapi .
```

**Run FastAPI container:**
```bash
docker run -p 8000:8000 --env-file .env access-fastapi
```

## Migration Status

- ✅ Phase 1: Foundation Setup (Complete)
- 🔄 Phase 2: Schema Conversion (Next)
- ⏳ Phase 3: Authentication & Middleware
- ⏳ Phase 4: API Endpoints Migration  
- ⏳ Phase 5: Testing & Validation
- ⏳ Phase 6: Deployment & Cutover

## Development Tips

1. **Auto-reload**: The development server automatically restarts when you change code files
2. **Interactive docs**: Visit `/api/v2/docs` to test endpoints directly in the browser
3. **Database**: Both Flask and FastAPI apps use the same SQLAlchemy models and database
4. **Debugging**: Set `echo=True` in `api_v2/database.py` to see SQL queries
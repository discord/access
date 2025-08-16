# Flask to FastAPI Migration Plan

## Overview

This document outlines the comprehensive migration plan from Flask/Marshmallow to FastAPI/Pydantic for the access management system.

## Current Architecture Analysis

### Flask Stack
- Flask with Flask-RESTful for API endpoints
- Marshmallow for serialization/validation (14 schema files)
- SQLAlchemy 2.0 with Flask-SQLAlchemy
- Flask-OIDC and Cloudflare Access for authentication
- 68+ Flask routes across 9 view modules
- Complex polymorphic schemas for different group types

### Key Components
- **Models**: 6 core model files in `api/models/`
- **Views**: 9 view modules with Flask-RESTful resources
- **Schemas**: 14 Marshmallow schema files for serialization
- **Authentication**: Cloudflare Access and OIDC integration
- **Database**: PostgreSQL with SQLAlchemy ORM

## Migration Strategy

### Phase 1: Foundation Setup (1-2 weeks)

#### 1.1 Install FastAPI dependencies
```bash
pip install fastapi uvicorn pydantic sqlalchemy alembic python-multipart
```

#### 1.2 Create parallel FastAPI app structure
```
api_v2/
├── main.py           # FastAPI app
├── dependencies.py   # Dependency injection
├── database.py       # Database connection
├── models/          # SQLAlchemy models (reuse existing)
├── schemas/         # Pydantic models
├── routers/         # FastAPI routers
├── middleware/      # Custom middleware
├── auth/            # Authentication logic
└── utils/           # Utility functions
```

#### 1.3 Database compatibility
- Keep existing SQLAlchemy models unchanged
- Create new database connection management for FastAPI
- Ensure database schema remains compatible during transition
- Update to use SQLAlchemy 2.0 async features gradually

#### 1.4 Basic FastAPI app setup
```python
# api_v2/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Access Management API",
    description="FastAPI version of access management system",
    version="2.0.0"
)

# Add middleware
app.add_middleware(CORSMiddleware, ...)
```

### Phase 2: Schema Conversion (2-3 weeks)

#### 2.1 Convert Marshmallow schemas to Pydantic
Priority order for conversion:

1. **`core_schemas.py`** → Core Pydantic models (users, groups, apps)
2. **`access_requests.py`** → Access request models  
3. **`role_requests.py`** → Role request models
4. **`pagination.py`** → Pagination models
5. **`audit_logs.py`** → Audit log models
6. **`metrics.py`** → Metrics models
7. Remaining specialized schemas

#### 2.2 Key conversion patterns
```python
# Before (Marshmallow)
from marshmallow import Schema, fields, validate

class UserSchema(Schema):
    email = fields.Email(required=True)
    first_name = fields.Str(validate=validate.Length(max=255))
    profile = fields.Dict()
    
    class Meta:
        fields = ("id", "email", "first_name", "profile")
        dump_only = ("id",)

# After (Pydantic)
from pydantic import BaseModel, Field, EmailStr
from typing import Dict, Any, Optional

class UserBase(BaseModel):
    email: EmailStr
    first_name: str = Field(max_length=255)
    profile: Dict[str, Any] = {}

class UserResponse(UserBase):
    id: str
    
    class Config:
        from_attributes = True

class UserCreate(UserBase):
    pass
```

#### 2.3 Handle complex nested relationships
```python
# Polymorphic group handling
from typing import Union, Literal
from pydantic import BaseModel, Field

class OktaGroupModel(BaseModel):
    type: Literal["okta_group"] = "okta_group"
    name: str
    description: str = ""
    # ... other fields

class RoleGroupModel(BaseModel):
    type: Literal["role_group"] = "role_group"
    name: str
    description: str = ""
    # ... other fields

class AppGroupModel(BaseModel):
    type: Literal["app_group"] = "app_group"
    name: str
    app_id: str
    # ... other fields

GroupModel = Union[OktaGroupModel, RoleGroupModel, AppGroupModel]
```

### Phase 3: Authentication & Middleware (1-2 weeks)

#### 3.1 Convert authentication system
```python
# api_v2/auth/dependencies.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> OktaUser:
    # Implement Cloudflare Access token validation
    # Or OIDC token validation
    pass
```

#### 3.2 Middleware migration
- **CORS** → FastAPI CORSMiddleware
- **Security headers** → Custom ASGI middleware
- **Error handling** → FastAPI exception handlers
- **Request logging** → Custom middleware

```python
# api_v2/middleware/security.py
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response
```

#### 3.3 Exception handling
```python
# api_v2/main.py
from fastapi import HTTPException
from fastapi.responses import JSONResponse

@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    return JSONResponse(
        status_code=400,
        content={"message": str(exc)}
    )
```

### Phase 4: API Endpoints Migration (3-4 weeks)

#### 4.1 Migration order by functional area
1. **Health check endpoints** (simplest, `health_check_views.py`)
2. **User endpoints** (`users_views.py`)
3. **Group endpoints** (`groups_views.py`) 
4. **App endpoints** (`apps_views.py`)
5. **Access request endpoints** (`access_requests_views.py`)
6. **Role endpoints and requests** (`roles_views.py`, `role_requests_views.py`)
7. **Tag and webhook endpoints** (`tags_views.py`, `webhook_views.py`)
8. **Audit endpoints** (`audit_views.py`)

#### 4.2 Example conversion pattern
```python
# Before (Flask-RESTful)
from flask_restful import Resource
from flask_apispec import doc, use_kwargs, marshal_with

class UserResource(Resource):
    @doc(description='Get user by ID')
    @marshal_with(user_schema)
    def get(self, user_id: str):
        user = OktaUser.query.get_or_404(user_id)
        return user

    @doc(description='Update user')
    @use_kwargs(user_schema)
    @marshal_with(user_schema)
    def put(self, user_id: str, **kwargs):
        user = OktaUser.query.get_or_404(user_id)
        for key, value in kwargs.items():
            setattr(user, key, value)
        db.session.commit()
        return user

# After (FastAPI)
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str, 
    db: Session = Depends(get_db),
    current_user: OktaUser = Depends(get_current_user)
):
    """Get user by ID"""
    user = db.query(OktaUser).filter(OktaUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    current_user: OktaUser = Depends(get_current_user)
):
    """Update user"""
    user = db.query(OktaUser).filter(OktaUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    for field, value in user_update.dict(exclude_unset=True).items():
        setattr(user, field, value)
    
    db.commit()
    db.refresh(user)
    return user
```

#### 4.3 Handle complex operations
```python
# Complex operations with multiple steps
@router.post("/groups", response_model=GroupResponse)
async def create_group(
    group_data: GroupCreate,
    db: Session = Depends(get_db),
    current_user: OktaUser = Depends(get_current_user)
):
    """Create new group with validation and constraints"""
    # Validate constraints
    # Create group
    # Handle tags
    # Return response
    pass
```

### Phase 5: Testing & Validation (1-2 weeks)

#### 5.1 Update test suite
```python
# Before (Flask testing)
def test_get_user(client):
    response = client.get('/api/users/123')
    assert response.status_code == 200

# After (FastAPI testing)
from fastapi.testclient import TestClient

def test_get_user():
    client = TestClient(app)
    response = client.get('/api/v2/users/123')
    assert response.status_code == 200
```

#### 5.2 Test conversion priorities
1. **Core functionality tests** (user CRUD, group management)
2. **Authentication tests** (token validation, permissions)
3. **Complex workflow tests** (access requests, role assignments)
4. **Integration tests** (database operations, external services)
5. **Performance tests** (response times, concurrent requests)

#### 5.3 API documentation validation
- Leverage FastAPI's automatic OpenAPI generation
- Validate all endpoints have proper documentation
- Ensure response models match actual responses
- Test API documentation UI (`/docs` and `/redoc`)

### Phase 6: Deployment & Cutover (1 week)

#### 6.1 Parallel deployment strategy
```yaml
# docker-compose.yml
version: '3.8'
services:
  flask-app:
    build: .
    ports:
      - "5000:5000"
    
  fastapi-app:
    build: 
      context: .
      dockerfile: Dockerfile.fastapi
    ports:
      - "8000:8000"
    
  nginx:
    image: nginx
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
```

#### 6.2 Traffic migration plan
1. **Phase 6a**: Deploy FastAPI alongside Flask
2. **Phase 6b**: Route health checks and read-only endpoints to FastAPI
3. **Phase 6c**: Gradually migrate write operations
4. **Phase 6d**: Full cutover with Flask app as fallback
5. **Phase 6e**: Remove Flask app after validation period

#### 6.3 Monitoring and rollback
- Monitor response times and error rates
- Set up alerts for FastAPI-specific metrics
- Prepare rollback procedures
- Document cutover process and rollback triggers

#### 6.4 Cleanup
- Remove Flask dependencies from requirements.txt
- Clean up unused Marshmallow schemas
- Update CI/CD pipelines for FastAPI
- Update documentation and README files

## Key Challenges & Solutions

### 1. Polymorphic Schema Handling
**Challenge**: `PolymorphicGroupSchema` handles different group types dynamically

**Solution**: Use Pydantic discriminated unions:
```python
from pydantic import BaseModel, Field
from typing import Union, Literal

class BaseGroup(BaseModel):
    name: str
    description: str = ""

class OktaGroup(BaseGroup):
    type: Literal["okta_group"] = "okta_group"

class RoleGroup(BaseGroup):
    type: Literal["role_group"] = "role_group"

class AppGroup(BaseGroup):
    type: Literal["app_group"] = "app_group"
    app_id: str

Group = Union[OktaGroup, RoleGroup, AppGroup] = Field(discriminator='type')
```

### 2. Complex Nested Relationships
**Challenge**: Deep nested serialization with selective field inclusion

**Solution**: Use Pydantic's response models with selective serialization:
```python
from pydantic import BaseModel
from typing import Optional, List

class UserGroupMembership(BaseModel):
    is_owner: bool
    created_at: datetime
    group: Optional[GroupSummary] = None
    
    class Config:
        from_attributes = True

class UserDetail(BaseModel):
    id: str
    email: str
    memberships: List[UserGroupMembership] = []
    
    class Config:
        from_attributes = True
```

### 3. Custom Validation Logic
**Challenge**: Complex cross-field validation in Marshmallow

**Solution**: Pydantic validators and model validators:
```python
from pydantic import BaseModel, validator, root_validator

class GroupCreate(BaseModel):
    name: str
    app_id: Optional[str] = None
    
    @validator('name')
    def name_must_match_pattern(cls, v, values):
        # Custom validation logic
        return v
    
    @root_validator
    def validate_app_group_requirements(cls, values):
        # Cross-field validation
        return values
```

### 4. Authentication Integration
**Challenge**: Maintaining existing Cloudflare Access and OIDC authentication

**Solution**: Create FastAPI-compatible auth dependencies:
```python
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

security = HTTPBearer()

async def verify_cloudflare_access(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> OktaUser:
    # Implement existing Cloudflare validation logic
    pass

async def verify_oidc_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> OktaUser:
    # Implement existing OIDC validation logic
    pass
```

## Benefits After Migration

### 1. Performance Improvements
- **Faster request handling**: FastAPI is built on Starlette/ASGI
- **Async support**: Better handling of I/O operations
- **Automatic validation**: Pydantic validation is faster than Marshmallow

### 2. Developer Experience
- **Type safety**: Better IDE support and autocomplete
- **Automatic documentation**: Built-in OpenAPI/Swagger generation
- **Modern Python**: Support for modern Python features and type hints
- **Better error messages**: More informative validation errors

### 3. API Documentation
- **Interactive docs**: Built-in Swagger UI at `/docs`
- **Alternative docs**: ReDoc interface at `/redoc`
- **OpenAPI spec**: Automatically generated and always up-to-date

### 4. Maintenance Benefits
- **Reduced boilerplate**: Less code for common patterns
- **Better testing**: Improved test client and async testing support
- **Modern ecosystem**: Better integration with modern Python tools

## Risk Mitigation

### 1. Data Consistency
- Keep existing database schema unchanged during migration
- Implement comprehensive testing for all CRUD operations
- Use transaction rollback for any data corruption issues

### 2. Authentication Security
- Thoroughly test all authentication paths
- Maintain existing security headers and CORS policies  
- Validate JWT token handling matches existing behavior

### 3. API Compatibility
- Maintain existing API contracts during transition
- Use API versioning (`/api/v2/`) for new FastAPI endpoints
- Document any breaking changes clearly

## Timeline Summary

| Phase | Duration | Key Deliverables |
|-------|----------|------------------|
| Phase 1: Foundation | 1-2 weeks | FastAPI app structure, database setup |
| Phase 2: Schemas | 2-3 weeks | All Pydantic models converted |
| Phase 3: Auth & Middleware | 1-2 weeks | Authentication system working |
| Phase 4: API Endpoints | 3-4 weeks | All endpoints migrated and tested |
| Phase 5: Testing | 1-2 weeks | Complete test suite updated |
| Phase 6: Deployment | 1 week | Production deployment and cutover |

**Total Estimated Timeline: 8-12 weeks**

## Success Criteria

- [ ] All existing API functionality preserved
- [ ] Authentication and authorization working correctly
- [ ] Performance improvements measurable
- [ ] Complete test coverage maintained
- [ ] API documentation automatically generated
- [ ] Zero data loss during migration
- [ ] Rollback plan tested and ready

## Next Steps

1. **Stakeholder approval** of migration plan
2. **Environment setup** for FastAPI development
3. **Team training** on FastAPI and Pydantic
4. **Begin Phase 1** implementation
5. **Set up monitoring** for parallel deployment

---

*This migration plan should be reviewed and approved by all stakeholders before implementation begins.*
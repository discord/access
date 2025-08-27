# Operations Migration Guide: Flask-SQLAlchemy to Pure SQLAlchemy

This guide outlines the key patterns and steps for converting operations from the Flask app (`api/`) to the FastAPI app (`api_v2/`).

## Key Conversion Patterns

### 1. Constructor Changes

**Before (Flask):**
```python
class CreateGroup:
    def __init__(
        self,
        *,
        group: T | GroupDict,
        tags: list[str] = [],
        current_user_id: Optional[str] = None,
    ):
        # Uses Flask's db.session implicitly
```

**After (FastAPI):**
```python
class CreateGroup:
    def __init__(
        self,
        db: Session,  # Add Session as first parameter
        *,
        group: T | GroupDict,
        tags: list[str] = [],
        current_user_id: Optional[str] = None,
        request: Optional[Request] = None,  # Add optional Request for audit logging
    ):
        self.db = db  # Store session for use throughout the class
        self.request = request
```

### 2. Database Query Replacements

| Flask Pattern | FastAPI Pattern | Notes |
|--------------|-----------------|-------|
| `Model.query` | `self.db.query(Model)` | Replace all class-level query access |
| `db.session.query()` | `self.db.query()` | Use injected session |
| `db.session.add()` | `self.db.add()` | Use injected session |
| `db.session.commit()` | `self.db.commit()` | Use injected session |
| `db.session.get()` | `self.db.get()` | Use injected session |
| `db.session.rollback()` | `self.db.rollback()` | Use injected session |

### 3. SQLAlchemy Function Imports

**Before (Flask):**
```python
from api.extensions import db
# Uses db.func, db.or_, db.and_, etc.
```

**After (FastAPI):**
```python
from sqlalchemy import func, or_, and_, text
from sqlalchemy.orm import Session
# Use SQLAlchemy functions directly
```

### 4. Model Imports

**Before (Flask):**
```python
from api.models import OktaGroup, OktaUser, AppGroup
```

**After (FastAPI):**
```python
from api_v2.models import OktaGroup, OktaUser, AppGroup
```

### 5. Service Imports

**Before (Flask):**
```python
from api.services import okta
```

**After (FastAPI):**
```python
from api_v2.services import okta
```

### 6. Authorization Helpers

**Before (Flask):**
```python
from api.authorization import AuthorizationHelpers

# Usage:
AuthorizationHelpers.is_access_admin(user_id)
```

**After (FastAPI):**
```python
from api_v2.auth.authorization import AuthorizationHelpers

# Usage - pass Session:
AuthorizationHelpers.is_access_admin(self.db, current_user)
```

### 7. Audit Logging

**Before (Flask):**
```python
from flask import current_app, has_request_context, request
from api.views.schemas import AuditLogSchema, EventType

# In execute method:
context = has_request_context()
current_app.logger.info(
    AuditLogSchema().dumps({
        "event_type": EventType.group_create,
        "user_agent": request.headers.get("User-Agent") if context else None,
        "ip": request.headers.get("X-Forwarded-For", request.headers.get("X-Real-IP", request.remote_addr))
        if context else None,
        # ... other fields
    })
)
```

**After (FastAPI):**
```python
from fastapi import Request
from api_v2.schemas import AuditEventType, AuditLogRead, AuditGroupSummary

# In constructor:
def __init__(self, ..., request: Optional[Request] = None):
    self.request = request

# Create private audit method:
def _log_audit_event(self, ...):
    audit_data = {
        "event_type": AuditEventType.GROUP_CREATE,
        "user_agent": None,
        "ip": None,
        # ... other fields
    }
    
    if self.request:
        audit_data["user_agent"] = self.request.headers.get("User-Agent")
        audit_data["ip"] = (
            self.request.headers.get("X-Forwarded-For") or
            self.request.headers.get("X-Real-IP") or
            self.request.client.host if self.request.client else None
        )
    
    audit_log = AuditLogRead(**audit_data)
    logger.info(audit_log.model_dump_json(exclude_none=True))
```

### 8. Flask Context Removal

Remove all Flask-specific context handling:
- `from flask import current_app, has_request_context, request`
- `current_app.logger` → `logger` (using Python's standard logging)
- `has_request_context()` checks → Use optional `request` parameter

### 9. Helper Function Updates

When operations use helper functions that query the database:

**Before (Flask):**
```python
def get_group_managers(group_id: str) -> List[OktaUser]:
    return OktaUser.query.filter(...).all()
```

**After (FastAPI):**
```python
def get_group_managers(db: Session, group_id: str) -> List[OktaUser]:
    return db.query(OktaUser).filter(...).all()
```

## Complete Example: CreateGroup Operation

### Original Flask Version Structure:
```python
from flask import current_app, has_request_context, request
from api.extensions import db
from api.models import OktaGroup

class CreateGroup:
    def __init__(self, *, group, current_user_id=None):
        self.group = group
        self.current_user_id = current_user_id
    
    def execute(self):
        existing = OktaGroup.query.filter(...).first()
        db.session.add(self.group)
        db.session.commit()
        # Audit logging with Flask context
```

### Converted FastAPI Version Structure:
```python
from fastapi import Request
from sqlalchemy.orm import Session
from api_v2.models import OktaGroup
from api_v2.schemas import AuditEventType, AuditLogRead

class CreateGroup:
    def __init__(self, db: Session, *, group, current_user_id=None, request: Optional[Request] = None):
        self.db = db
        self.request = request
        self.group = group
        self.current_user_id = current_user_id
    
    def _log_audit_event(self, group_id: str):
        # Structured audit logging
        pass
    
    def execute(self):
        existing = self.db.query(OktaGroup).filter(...).first()
        self.db.add(self.group)
        self.db.commit()
        self._log_audit_event(self.group.id)
```

## Checklist for Converting an Operation

- [ ] Add `db: Session` as the first constructor parameter
- [ ] Add `request: Optional[Request] = None` to constructor if audit logging is used
- [ ] Store `self.db = db` and `self.request = request` in constructor
- [ ] Replace all `Model.query` with `self.db.query(Model)`
- [ ] Replace all `db.session.*` with `self.db.*`
- [ ] Update imports from `api.*` to `api_v2.*`
- [ ] Remove Flask imports (`flask`, `current_app`, etc.)
- [ ] Import SQLAlchemy functions directly instead of through `db`
- [ ] Convert audit logging to use `AuditLogRead` schema
- [ ] Extract audit logging to a private method
- [ ] Update any helper function calls to pass `self.db` as first parameter
- [ ] Update authorization checks to pass `self.db` as needed
- [ ] Test that no Flask-SQLAlchemy dependencies remain

## Common Gotchas

1. **Lazy Loading**: FastAPI models use `lazy="raise"` by default, so ensure proper eager loading with `joinedload()` or `selectinload()`
2. **Session Scope**: The session is request-scoped in FastAPI, passed explicitly rather than using a global
3. **Polymorphic Queries**: Use `with_polymorphic()` or `selectin_polymorphic()` for polymorphic models
4. **Audit Context**: Request information must be explicitly passed, not extracted from Flask context

## Testing the Conversion

After conversion, verify:
1. No imports from `api.extensions` or `flask`
2. All database operations use the injected session
3. Audit logs are properly structured as JSON
4. The operation works with FastAPI's dependency injection system
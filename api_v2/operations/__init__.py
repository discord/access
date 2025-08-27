"""
Operations for FastAPI.
Pure SQLAlchemy implementation without Flask dependencies.
"""

# Import constraints
from api_v2.operations.constraints import CheckForReason, CheckForSelfAdd

# Note: The full operations conversion is a large undertaking.
# Each operation needs to be converted to:
# 1. Accept a Session parameter in the constructor
# 2. Replace all db.session usage with the passed Session
# 3. Replace Model.query with session.query(Model)
# 4. Remove Flask dependencies (current_app, request, etc.)
# 5. Update imports to use api_v2.models

# For now, we're providing the constraint operations which are commonly used.
# The remaining operations should be converted as needed following the same pattern.

__all__ = [
    "CheckForReason",
    "CheckForSelfAdd",
]
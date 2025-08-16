"""
Base Pydantic schemas and utilities for the FastAPI app.
These provide common patterns and base classes for all other schemas.
"""
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class BaseSchema(BaseModel):
    """
    Base Pydantic model with common configuration.
    All other schemas should inherit from this.
    """
    model_config = ConfigDict(
        # Allow SQLAlchemy models to be used directly
        from_attributes=True,
        # Use enum values instead of names in JSON
        use_enum_values=True,
        # Validate assignment to ensure data integrity
        validate_assignment=True,
        # Don't allow extra fields that aren't defined
        extra='forbid'
    )


class TimestampMixin(BaseModel):
    """
    Mixin for models that have timestamp fields.
    Most database models inherit these fields.
    """
    created_at: datetime = Field(..., description="When the record was created")
    updated_at: datetime = Field(..., description="When the record was last updated")
    deleted_at: Optional[datetime] = Field(None, description="When the record was deleted (soft delete)")


class IdMixin(BaseModel):
    """
    Mixin for models that have an ID field.
    """
    id: str = Field(..., description="Unique identifier")


class BaseReadSchema(BaseSchema, IdMixin, TimestampMixin):
    """
    Base schema for reading/displaying database records.
    Includes ID and timestamp fields that are typically read-only.
    """
    pass


class BaseCreateSchema(BaseSchema):
    """
    Base schema for creating new records.
    Excludes ID and timestamp fields that are auto-generated.
    """
    pass


class BaseUpdateSchema(BaseSchema):
    """
    Base schema for updating existing records.
    All fields are optional to support partial updates.
    """
    pass


# Common field types and validators
ProfileDict = Dict[str, Any]  # For Okta user profiles
ConstraintsDict = Dict[str, Any]  # For tag constraints
ExternallyManagedDict = Dict[str, Any]  # For externally managed data
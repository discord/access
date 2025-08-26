"""
Base model configuration for FastAPI SQLAlchemy models.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Define naming conventions for database constraints
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=naming_convention)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models"""
    metadata = metadata
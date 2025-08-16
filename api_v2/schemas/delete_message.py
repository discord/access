"""
FastAPI/Pydantic delete message schema.
"""
from pydantic import Field

from api_v2.schemas.base import BaseSchema


class DeleteMessage(BaseSchema):
    """
    Schema for delete operation responses.
    Simple confirmation message for successful deletions.
    """
    deleted: bool = Field(True, description="Indicates successful deletion")
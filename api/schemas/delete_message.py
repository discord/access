from pydantic import BaseModel


class DeleteMessage(BaseModel):
    deleted: bool

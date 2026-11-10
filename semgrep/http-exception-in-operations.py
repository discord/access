import fastapi
from fastapi import HTTPException

from api.exceptions import ConflictError


class CreateThing:
    async def execute(self):
        if self.exists:
            # ruleid: http-exception-in-operations
            raise HTTPException(409, "Thing already exists")
        if self.gone:
            # ruleid: http-exception-in-operations
            raise fastapi.HTTPException(status_code=410, detail="Thing is gone")
        if self.conflict:
            # ok: http-exception-in-operations
            raise ConflictError("Thing already exists")

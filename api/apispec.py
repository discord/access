from typing import Any, Callable, Optional, TypeVar, cast

from flask import Flask
from flask_apispec import MethodResource, marshal_with, use_kwargs
from flask_apispec.extension import FlaskApiSpec
from marshmallow import Schema


class FlaskApiSpecExt:
    """Very simple and small extension to use apispec with this API as a flask extension"""

    def __init__(self, app:  Optional[Flask] = None, **kwargs: dict[str, Any]) -> None:
        if app is not None:
            self.init_app(app, **kwargs)

    def init_app(self, app: Flask, **kwargs: dict[str, Any]) -> None:
        app.config.setdefault("APISPEC_TITLE", "access")
        app.config.setdefault("APISPEC_VERSION", "1.0.0")
        app.config.setdefault("APISPEC_SWAGGER_URL", "/api/swagger.json")
        app.config.setdefault("APISPEC_SWAGGER_UI_URL", "/api/swagger-ui")

        self.spec = FlaskApiSpec(app)

    def register(
        self,
        target: MethodResource,
        endpoint: Optional[str] = None,
        blueprint: Optional[str] = None,
    ) -> None:
        self.spec.register(
            target, endpoint, blueprint
        )


F = TypeVar("F", bound=Callable[..., Any])

class FlaskApiSpecDecorators:
    @staticmethod
    def request_schema(schema: type[Schema], **kwargs: str) -> Callable[[F], F]:
        def wrapper(func: F) -> F:
            return cast(F, use_kwargs(schema, apply=False, **kwargs)(func))

        return wrapper

    @staticmethod
    def response_schema(schema: type[Schema], **kwargs: str) -> Callable[[F], F]:
        def wrapper(func: F) -> F:
            return cast(F, marshal_with(schema, apply=False, **kwargs)(func))

        return wrapper

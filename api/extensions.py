from typing import Callable, Optional, ParamSpec, Tuple

from flask import jsonify
from flask_marshmallow import Marshmallow
from flask_migrate import Migrate
from flask_oidc import OpenIDConnect
from flask_restful import Api as _Api
from flask_sqlalchemy import SQLAlchemy
from google.cloud.sql.connector import Connector, IPTypes
from marshmallow import exceptions
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase
from werkzeug.wrappers import Response

from api.apispec import FlaskApiSpecExt


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


db = SQLAlchemy(model_class=Base)
ma = Marshmallow()
migrate = Migrate(render_as_batch=True)
docs = FlaskApiSpecExt()
oidc = OpenIDConnect()

P = ParamSpec("P")


class Api(_Api):
    def error_router(
        self, original_handler: Callable[P, Tuple[Response, int]], e: BaseException
    ) -> Tuple[Response, int]:
        if isinstance(e, exceptions.ValidationError):
            if isinstance(e.messages, list):
                return jsonify(message=e.messages[0]), 400
            elif isinstance(e.messages, dict):
                return jsonify(
                    message=list(e.normalized_messages().values())[0][0]  # type: ignore[no-untyped-call]
                ), 400
            else:
                return jsonify(message=e.messages), 400

        return super().error_router(original_handler, e)


def get_cloudsql_conn(
    cloudsql_connection_name: str,
    db_user: Optional[str] = "root",
    db_name: Optional[str] = "access",
    uses_public_ip: Optional[bool] = False,
) -> Callable[[], Connector]:
    def _get_conn() -> Connector:
        with Connector() as connector:
            conn = connector.connect(
                cloudsql_connection_name,  # Cloud SQL Instance Connection Name
                "pg8000",
                user=db_user,
                db=db_name,
                ip_type=IPTypes.PUBLIC if uses_public_ip else IPTypes.PRIVATE,
                enable_iam_auth=True,
            )
            return conn

    return _get_conn

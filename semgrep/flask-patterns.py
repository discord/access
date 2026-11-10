# ruleid: flask-patterns
import flask
# ruleid: flask-patterns
from flask import g

from fastapi import HTTPException


# ruleid: flask-patterns
@app.before_request
def load_user():
    # ruleid: flask-patterns
    flask.g.user = current_user()


def get_group(group_id):
    if group_id is None:
        # ruleid: flask-patterns
        abort(404)
    if group_id == "":
        # ruleid: flask-patterns
        flask.abort(400)
    if group_id == "gone":
        # ok: flask-patterns
        raise HTTPException(404, "Group not found")

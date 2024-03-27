from marshmallow import Schema, fields


class DeleteMessageSchema(Schema):
    deleted = fields.Boolean(dump_only=True)

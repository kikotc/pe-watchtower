from peewee import AutoField, CharField, DateTimeField, ForeignKeyField, TextField

from app.database import BaseModel
from app.models.url import Url
from app.models.user import User


class Event(BaseModel):
    id = AutoField()
    url = ForeignKeyField(Url, backref="events", on_delete="CASCADE")
    user = ForeignKeyField(User, backref="events", on_delete="CASCADE", null=True)
    event_type = CharField()
    timestamp = DateTimeField()
    details = TextField(default="{}")

    class Meta:
        table_name = "events"

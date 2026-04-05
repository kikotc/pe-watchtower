from peewee import AutoField, CharField, DateTimeField, FloatField

from app.database import BaseModel


class Incident(BaseModel):
    id = AutoField()
    incident_type = CharField()       # "service_down" | "high_error_rate"
    started_at = DateTimeField()
    resolved_at = DateTimeField(null=True)
    duration_seconds = FloatField(null=True)
    details = CharField(default="")

    class Meta:
        table_name = "incidents"

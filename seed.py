import csv
import os
import sys

from dotenv import load_dotenv
from peewee import PostgresqlDatabase

load_dotenv()

# Set up the database connection before importing models
from app.database import db

database = PostgresqlDatabase(
    os.environ.get("DATABASE_NAME", "hackathon_db"),
    host=os.environ.get("DATABASE_HOST", "localhost"),
    port=int(os.environ.get("DATABASE_PORT", 5432)),
    user=os.environ.get("DATABASE_USER", "postgres"),
    password=os.environ.get("DATABASE_PASSWORD", "postgres"),
)
db.initialize(database)

from app.models.user import User
from app.models.url import Url
from app.models.event import Event

SEED_DIR = os.path.join(os.path.dirname(__file__), "seed_data")
BATCH_SIZE = 100


def seed():
    db.connect(reuse_if_open=True)

    # Create tables (safe — won't drop existing)
    db.create_tables([User, Url, Event], safe=True)

    # --- Users ---
    if User.select().count() == 0:
        print("Seeding users...")
        with open(os.path.join(SEED_DIR, "users.csv")) as f:
            reader = csv.DictReader(f)
            rows = [
                {
                    "id": int(r["id"]),
                    "username": r["username"],
                    "email": r["email"],
                    "created_at": r["created_at"],
                }
                for r in reader
            ]
        with db.atomic():
            for i in range(0, len(rows), BATCH_SIZE):
                User.insert_many(rows[i : i + BATCH_SIZE]).execute()
        print(f"  Inserted {len(rows)} users.")
    else:
        print("Users table already has data, skipping.")

    # --- URLs ---
    if Url.select().count() == 0:
        print("Seeding urls...")
        with open(os.path.join(SEED_DIR, "urls.csv")) as f:
            reader = csv.DictReader(f)
            rows = [
                {
                    "id": int(r["id"]),
                    "user": int(r["user_id"]),
                    "short_code": r["short_code"],
                    "original_url": r["original_url"],
                    "title": r["title"],
                    "is_active": r["is_active"] == "True",
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in reader
            ]
        with db.atomic():
            for i in range(0, len(rows), BATCH_SIZE):
                Url.insert_many(rows[i : i + BATCH_SIZE]).execute()
        print(f"  Inserted {len(rows)} urls.")
    else:
        print("URLs table already has data, skipping.")

    # --- Events ---
    if Event.select().count() == 0:
        print("Seeding events...")
        with open(os.path.join(SEED_DIR, "events.csv")) as f:
            reader = csv.DictReader(f)
            rows = [
                {
                    "id": int(r["id"]),
                    "url": int(r["url_id"]),
                    "user": int(r["user_id"]),
                    "event_type": r["event_type"],
                    "timestamp": r["timestamp"],
                    "details": r["details"],
                }
                for r in reader
            ]
        with db.atomic():
            for i in range(0, len(rows), BATCH_SIZE):
                Event.insert_many(rows[i : i + BATCH_SIZE]).execute()
        print(f"  Inserted {len(rows)} events.")
    else:
        print("Events table already has data, skipping.")

    # Reset sequences so new inserts get IDs after the seeded data
    for table in ["users", "urls", "events"]:
        db.execute_sql(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1));"
        )
    print("Sequences reset.")

    db.close()
    print("Done!")


if __name__ == "__main__":
    seed()

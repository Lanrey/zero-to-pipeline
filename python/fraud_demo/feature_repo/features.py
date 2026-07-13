"""Feast feature definitions for fraud detection.

Three feature views — matching the Feast fraud tutorial schema
but backed by local parquet files instead of BigQuery.

  user_account_features      — credit score, account age, 2FA status
  user_transaction_counts    — 7-day rolling transaction count per user
  user_transaction_stats     — derived from the transactions parquet
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from feast import Entity, FeatureView, Field, FileSource
from feast.types import Bool, Float32, Int32, Int64

DATA = str(Path(__file__).parent.parent / "data")

# ── Entity ────────────────────────────────────────────────────────────────────

user = Entity(
    name="user_id",
    description="A user that has executed or received a transaction",
    join_keys=["user_id"],
)

# ── Data sources ──────────────────────────────────────────────────────────────

account_source = FileSource(
    path=f"{DATA}/user_account_features.parquet",
    timestamp_field="event_timestamp",
)

counts_source = FileSource(
    path=f"{DATA}/user_transaction_counts.parquet",
    timestamp_field="event_timestamp",
)

# ── Feature views ─────────────────────────────────────────────────────────────

user_account_features = FeatureView(
    name="user_account_features",
    entities=[user],
    ttl=timedelta(weeks=52),
    schema=[
        Field(name="credit_score",          dtype=Int32),
        Field(name="account_age_days",       dtype=Int32),
        Field(name="user_has_2fa_installed", dtype=Bool),
        Field(name="avg_transaction_amount", dtype=Float32),
    ],
    source=account_source,
    tags={"team": "fraud", "domain": "user_profile"},
)

user_transaction_counts = FeatureView(
    name="user_transaction_counts",
    entities=[user],
    ttl=timedelta(weeks=1),
    schema=[
        Field(name="transaction_count_7d", dtype=Int64),
    ],
    source=counts_source,
    tags={"team": "fraud", "domain": "behaviour"},
)

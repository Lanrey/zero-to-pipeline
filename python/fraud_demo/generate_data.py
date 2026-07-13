"""Generate synthetic fraud detection dataset.

Context: EuroPython 2026 — Zero to Pipeline talk.
Use case: Real-time fraud detection for a fintech platform.

Dataset size chosen to:
- Run fast enough for a live demo (< 3 seconds to generate)
- Be large enough to show realistic patterns (1,000 users, 15,000 transactions)
- Include a realistic fraud rate (~2.5%) matching real-world card fraud rates
- Span 6 months of history so incremental sync is meaningful

Produces three parquet files (matching the Feast fraud tutorial schema):
  data/transactions.parquet       — raw transaction events
  data/user_account_features.parquet  — user credit/account profile
  data/user_transaction_counts.parquet — 7-day rolling transaction counts
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
rng = np.random.default_rng(SEED)
random.seed(SEED)

OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)

N_USERS = 1_000
N_TRANSACTIONS = 15_000
FRAUD_RATE = 0.025          # 2.5% — realistic card fraud rate
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END   = datetime(2026, 7, 1, tzinfo=timezone.utc)
SPAN_SECONDS = int((END - START).total_seconds())

# ── User profiles ─────────────────────────────────────────────────────────────

user_ids = [f"user_{i:04d}" for i in range(N_USERS)]

account_ages = rng.integers(30, 3650, size=N_USERS)        # 30 days – 10 years
credit_scores = rng.integers(300, 850, size=N_USERS)
has_2fa = rng.choice([True, False], size=N_USERS, p=[0.6, 0.4])
avg_tx_amount = rng.uniform(20, 500, size=N_USERS)          # typical spend per user

user_df = pd.DataFrame({
    "user_id":           user_ids,
    "credit_score":      credit_scores.astype(int),
    "account_age_days":  account_ages.astype(int),
    "user_has_2fa_installed": has_2fa,
    "avg_transaction_amount": np.round(avg_tx_amount, 2),
    "event_timestamp":   [END] * N_USERS,   # static profile snapshot
    "created":           [START] * N_USERS,
})
user_df.to_parquet(OUT / "user_account_features.parquet", index=False)
print(f"  ✓ user_account_features.parquet  — {len(user_df):,} users")

# ── Transactions ──────────────────────────────────────────────────────────────

tx_user_indices = rng.integers(0, N_USERS, size=N_TRANSACTIONS)
tx_user_ids = [user_ids[i] for i in tx_user_indices]

# Timestamp: random within the 6-month window
tx_offsets = rng.integers(0, SPAN_SECONDS, size=N_TRANSACTIONS)
tx_timestamps = [START + timedelta(seconds=int(s)) for s in sorted(tx_offsets)]

# Amount: users have a typical spend; fraud transactions are outliers
tx_amounts = np.array([
    avg_tx_amount[tx_user_indices[i]] * rng.uniform(0.5, 2.0)
    for i in range(N_TRANSACTIONS)
])

# Fraud label: base rate + higher risk for:
#   - new accounts (age < 90 days)
#   - low credit score (< 500)
#   - no 2FA
#   - unusually high amount (> 3× typical)
fraud_prob = np.full(N_TRANSACTIONS, FRAUD_RATE)
for i, uid_idx in enumerate(tx_user_indices):
    if account_ages[uid_idx] < 90:
        fraud_prob[i] += 0.04
    if credit_scores[uid_idx] < 500:
        fraud_prob[i] += 0.03
    if not has_2fa[uid_idx]:
        fraud_prob[i] += 0.02
    if tx_amounts[i] > avg_tx_amount[uid_idx] * 3:
        fraud_prob[i] += 0.05
fraud_prob = np.clip(fraud_prob, 0, 0.35)

tx_is_fraud = rng.random(N_TRANSACTIONS) < fraud_prob

# Merchant categories
categories = ["grocery", "electronics", "travel", "restaurant",
              "online_retail", "gas_station", "atm_withdrawal"]
cat_weights = [0.25, 0.15, 0.10, 0.20, 0.15, 0.10, 0.05]
tx_categories = rng.choice(categories, size=N_TRANSACTIONS, p=cat_weights)

# Country: 85% domestic, 15% international (correlates with fraud)
countries = ["US"] * 85 + ["GB", "NG", "UA", "RU", "BR", "CN", "VN",
                            "IN", "MX", "RO", "DE", "FR", "CA", "AU"]
tx_countries = rng.choice(countries, size=N_TRANSACTIONS)
# Boost fraud for international
for i in range(N_TRANSACTIONS):
    if tx_countries[i] != "US" and rng.random() < 0.08:
        tx_is_fraud[i] = True

tx_df = pd.DataFrame({
    "transaction_id":    [f"tx_{i:06d}" for i in range(N_TRANSACTIONS)],
    "user_id":           tx_user_ids,
    "amount":            np.round(tx_amounts, 2),
    "merchant_category": tx_categories,
    "country":           tx_countries,
    "is_fraud":          tx_is_fraud.astype(int),
    "event_timestamp":   tx_timestamps,
})
tx_df.to_parquet(OUT / "transactions.parquet", index=False)
fraud_count = tx_is_fraud.sum()
print(f"  ✓ transactions.parquet           — {len(tx_df):,} transactions "
      f"({fraud_count} fraud, {fraud_count/N_TRANSACTIONS:.1%})")

# ── 7-day rolling transaction counts ─────────────────────────────────────────
# For each user, compute how many transactions they made in the 7 days
# before each date in the dataset. We snapshot this at END for online serving.

tx_df_sorted = tx_df.sort_values("event_timestamp")
counts = []
for uid in user_ids:
    user_txs = tx_df_sorted[tx_df_sorted["user_id"] == uid]["event_timestamp"]
    window_start = END - timedelta(days=7)
    count_7d = int((user_txs >= window_start).sum())
    counts.append(count_7d)

count_df = pd.DataFrame({
    "user_id":               user_ids,
    "transaction_count_7d":  counts,
    "event_timestamp":       [END] * N_USERS,
})
count_df.to_parquet(OUT / "user_transaction_counts.parquet", index=False)
print(f"  ✓ user_transaction_counts.parquet — {len(count_df):,} user snapshots")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n  Dataset summary:")
print(f"    Users:        {N_USERS:,}")
print(f"    Transactions: {N_TRANSACTIONS:,}")
print(f"    Fraud rate:   {tx_is_fraud.sum() / N_TRANSACTIONS:.1%}")
print(f"    Date range:   {START.date()} → {END.date()}")
print(f"    Output:       {OUT.resolve()}")

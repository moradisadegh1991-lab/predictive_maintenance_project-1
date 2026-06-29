"""
Predictive Maintenance — "will this equipment fail in the next 30 days?"

Pipeline:
  1. Build a monthly snapshot table per equipment (features as-of date + label)
     -- features use ONLY past data (no leakage)
  2. Time-based train/test split (train = earlier months, test = later months)
  3. Train a RandomForest classifier (handles class imbalance via class_weight)
  4. Evaluate with precision/recall/ROC-AUC (NOT accuracy -- data is imbalanced)
  5. Inspect feature importances -> what the model actually relies on

intermediate-Python friendly; domain variable names throughout.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, roc_auc_score, confusion_matrix

BASE = "/mnt/user-data/outputs"
HORIZON_DAYS = 30          # prediction window
FAILURE_TYPES = [2, 4]     # Corrective, Breakdown

# ----------------------------------------------------------------------
# Load data
# ----------------------------------------------------------------------
work_orders = pd.read_csv(f"{BASE}/Fact_WorkOrders.csv", parse_dates=["FailureDate"])
production  = pd.read_csv(f"{BASE}/Fact_Production.csv")
equipment   = pd.read_csv(f"{BASE}/Dim_Equipment.csv", parse_dates=["InstallDate"])

failures = work_orders[work_orders["MaintenanceTypeID"].isin(FAILURE_TYPES)]

# Convert production DateKey (YYYYMMDD int) -> real date, aggregate downtime per day
production["Date"] = pd.to_datetime(production["DateKey"], format="%Y%m%d")
daily_downtime = (production.groupby(["EquipmentID", "Date"])["DowntimeMinutes"]
                  .sum().reset_index())

# ----------------------------------------------------------------------
# 1. Build snapshot table (the heart of PdM feature engineering)
# ----------------------------------------------------------------------
# One snapshot at the start of each month, for each equipment.
snapshot_dates = pd.date_range("2024-02-01", "2025-11-01", freq="MS")

records = []
for _, eq in equipment.iterrows():
    eid = eq["EquipmentID"]
    eq_failures = failures.loc[failures["EquipmentID"] == eid, "FailureDate"].sort_values()
    eq_downtime = daily_downtime[daily_downtime["EquipmentID"] == eid]

    for snap in snapshot_dates:
        # --- LABEL: any failure in [snap, snap + horizon) ---
        horizon_end = snap + pd.Timedelta(days=HORIZON_DAYS)
        label = int(((eq_failures >= snap) & (eq_failures < horizon_end)).any())

        # --- FEATURES: built only from data strictly BEFORE the snapshot ---
        past = eq_failures[eq_failures < snap]

        days_since_last = (snap - past.max()).days if len(past) else 9999
        failures_prev_90d = ((past >= snap - pd.Timedelta(days=90)) & (past < snap)).sum()

        mask_30 = ((eq_downtime["Date"] >= snap - pd.Timedelta(days=30)) &
                   (eq_downtime["Date"] < snap))
        downtime_prev_30d = eq_downtime.loc[mask_30, "DowntimeMinutes"].sum()

        age_years = (snap - eq["InstallDate"]).days / 365.0

        records.append({
            "EquipmentID": eid,
            "SnapshotDate": snap,
            "AssetType": eq["AssetType"],
            "CriticalityClass": eq["CriticalityClass"],
            "IsSeawaterCooled": int(eq["IsSeawaterCooled"]),
            "AgeYears": round(age_years, 2),
            "DaysSinceLastFailure": days_since_last,
            "FailuresPrev90d": int(failures_prev_90d),
            "DowntimePrev30d": round(downtime_prev_30d, 1),
            "WillFail30d": label,
        })

snap_df = pd.DataFrame(records)
print(f"Snapshots: {len(snap_df)} | positive rate: {snap_df['WillFail30d'].mean():.1%}\n")

# ----------------------------------------------------------------------
# 2. Encode features. One-hot AssetType; ordinal-encode criticality (A<B<C risk).
#    PQ: this is like adding indicator columns / a conditional column.
# ----------------------------------------------------------------------
crit_map = {"A": 0, "B": 1, "C": 2}
snap_df["CriticalityRank"] = snap_df["CriticalityClass"].map(crit_map)
X = pd.get_dummies(
    snap_df.drop(columns=["EquipmentID", "CriticalityClass", "WillFail30d"]),
    columns=["AssetType"],
)

# ----------------------------------------------------------------------
# 3. TIME-BASED split (train on earlier, test on later) -> realistic, no leakage
# ----------------------------------------------------------------------
split_date = pd.Timestamp("2025-06-01")
train_mask = snap_df["SnapshotDate"] < split_date
X_train = X[train_mask].drop(columns=["SnapshotDate"])
X_test  = X[~train_mask].drop(columns=["SnapshotDate"])
y_train = snap_df.loc[train_mask, "WillFail30d"]
y_test  = snap_df.loc[~train_mask, "WillFail30d"]

# ----------------------------------------------------------------------
# 4. Train. class_weight="balanced" tells the model the rare class matters.
# ----------------------------------------------------------------------
model = RandomForestClassifier(
    n_estimators=300, max_depth=6, class_weight="balanced", random_state=42,
)
model.fit(X_train, y_train)

y_pred  = model.predict(X_test)
y_proba = model.predict_proba(X_test)[:, 1]

# ----------------------------------------------------------------------
# 5. Evaluate -- precision/recall/AUC, NOT accuracy
# ----------------------------------------------------------------------
print("=== TEST PERFORMANCE ===")
print(classification_report(y_test, y_pred, target_names=["No failure", "Failure"]))
print(f"ROC-AUC: {roc_auc_score(y_test, y_proba):.3f}")
print("Confusion matrix [rows=actual, cols=pred]:")
print(confusion_matrix(y_test, y_pred))

# ----------------------------------------------------------------------
# Feature importances -- what is the model leaning on?
# ----------------------------------------------------------------------
importances = (pd.Series(model.feature_importances_, index=X_train.columns)
               .sort_values(ascending=False))
print("\n=== FEATURE IMPORTANCE ===")
print(importances.round(3).to_string())

"""
Predictive Maintenance v2 -- adds sensor-trend features and compares
BEFORE (no sensors) vs AFTER (with sensors), so you can see the lift.

New features per snapshot, from the prior 30 days of sensor data:
  vibration: mean, max, slope (trend)   <- the key precursor signals
  bearing temp: mean, max
  pressure: min, slope
Slope = how fast the signal is trending up/down -> the real PdM signal.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, recall_score

BASE = "/mnt/user-data/outputs"
HORIZON_DAYS = 30
FAILURE_TYPES = [2, 4]

work_orders = pd.read_csv(f"{BASE}/Fact_WorkOrders.csv", parse_dates=["FailureDate"])
production  = pd.read_csv(f"{BASE}/Fact_Production.csv")
equipment   = pd.read_csv(f"{BASE}/Dim_Equipment.csv", parse_dates=["InstallDate"])
sensors     = pd.read_csv(f"{BASE}/Fact_SensorReadings.csv", parse_dates=["Date"])

failures = work_orders[work_orders["MaintenanceTypeID"].isin(FAILURE_TYPES)]
production["Date"] = pd.to_datetime(production["DateKey"], format="%Y%m%d")
daily_downtime = (production.groupby(["EquipmentID", "Date"])["DowntimeMinutes"]
                  .sum().reset_index())

# Pre-group sensors per equipment for fast windowed lookups
sensors_by_eq = {eid: g.sort_values("Date") for eid, g in sensors.groupby("EquipmentID")}


def slope(values):
    """Linear trend (units per day). Ignores NaN; needs >=2 valid points."""
    v = pd.Series(values).dropna()
    if len(v) < 2:
        return 0.0
    x = np.arange(len(v))
    return float(np.polyfit(x, v.values, 1)[0])


snapshot_dates = pd.date_range("2024-02-01", "2025-11-01", freq="MS")

records = []
for _, eq in equipment.iterrows():
    eid = eq["EquipmentID"]
    eq_failures = failures.loc[failures["EquipmentID"] == eid, "FailureDate"].sort_values()
    eq_downtime = daily_downtime[daily_downtime["EquipmentID"] == eid]
    eq_sensors  = sensors_by_eq[eid]

    for snap in snapshot_dates:
        horizon_end = snap + pd.Timedelta(days=HORIZON_DAYS)
        label = int(((eq_failures >= snap) & (eq_failures < horizon_end)).any())

        past = eq_failures[eq_failures < snap]
        days_since_last = (snap - past.max()).days if len(past) else 9999
        failures_prev_90d = ((past >= snap - pd.Timedelta(days=90)) & (past < snap)).sum()

        mask_30 = ((eq_downtime["Date"] >= snap - pd.Timedelta(days=30)) &
                   (eq_downtime["Date"] < snap))
        downtime_prev_30d = eq_downtime.loc[mask_30, "DowntimeMinutes"].sum()

        # --- sensor window: prior 30 days, strictly before snapshot ---
        sw = eq_sensors[(eq_sensors["Date"] >= snap - pd.Timedelta(days=30)) &
                        (eq_sensors["Date"] < snap)]

        records.append({
            "EquipmentID": eid,
            "SnapshotDate": snap,
            "AssetType": eq["AssetType"],
            "CriticalityRank": {"A": 0, "B": 1, "C": 2}[eq["CriticalityClass"]],
            "IsSeawaterCooled": int(eq["IsSeawaterCooled"]),
            "AgeYears": round((snap - eq["InstallDate"]).days / 365.0, 2),
            "DaysSinceLastFailure": days_since_last,
            "FailuresPrev90d": int(failures_prev_90d),
            "DowntimePrev30d": round(downtime_prev_30d, 1),
            # sensor-trend features
            "Vib_mean": sw["Vibration_mms"].mean(),
            "Vib_max":  sw["Vibration_mms"].max(),
            "Vib_slope": slope(sw["Vibration_mms"]),
            "Temp_mean": sw["BearingTemp_C"].mean(),
            "Temp_max":  sw["BearingTemp_C"].max(),
            "Press_min": sw["Pressure_bar"].min(),
            "Press_slope": slope(sw["Pressure_bar"]),
            "WillFail30d": label,
        })

df = pd.DataFrame(records)
num_cols = df.select_dtypes(include="number").columns
df[num_cols] = df[num_cols].fillna(df[num_cols].median())

SENSOR_COLS = ["Vib_mean", "Vib_max", "Vib_slope", "Temp_mean", "Temp_max",
               "Press_min", "Press_slope"]
BASE_COLS   = ["CriticalityRank", "IsSeawaterCooled", "AgeYears",
               "DaysSinceLastFailure", "FailuresPrev90d", "DowntimePrev30d"]

# one-hot asset type, append to base
asset_dummies = pd.get_dummies(df["AssetType"], prefix="Asset")
X_all = pd.concat([df[BASE_COLS + SENSOR_COLS], asset_dummies], axis=1)

split_date = pd.Timestamp("2025-06-01")
train_mask = df["SnapshotDate"] < split_date
y_train, y_test = df["WillFail30d"][train_mask], df["WillFail30d"][~train_mask]


def evaluate(feature_cols, label):
    Xtr = X_all.loc[train_mask, feature_cols]
    Xte = X_all.loc[~train_mask, feature_cols]
    m = RandomForestClassifier(n_estimators=300, max_depth=6,
                               class_weight="balanced", random_state=42)
    m.fit(Xtr, y_train)
    proba = m.predict_proba(Xte)[:, 1]
    pred = (proba >= 0.5).astype(int)
    auc = roc_auc_score(y_test, proba)
    rec = recall_score(y_test, pred)
    print(f"{label:28s} ROC-AUC={auc:.3f}   Failure recall={rec:.2f}")
    return m


print("=== BEFORE vs AFTER adding sensor features ===")
baseline_cols = BASE_COLS + list(asset_dummies.columns)
full_cols     = BASE_COLS + SENSOR_COLS + list(asset_dummies.columns)
evaluate(baseline_cols, "WITHOUT sensors (v1)")
model_full = evaluate(full_cols, "WITH sensors (v2)")

imp = (pd.Series(model_full.feature_importances_, index=full_cols)
       .sort_values(ascending=False).head(8))
print("\n=== TOP FEATURES (with sensors) ===")
print(imp.round(3).to_string())

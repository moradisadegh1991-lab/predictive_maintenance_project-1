"""
Maintenance KPIs in Pandas — the same MTBF / MTTR / OEE you built in DAX.

Each block is annotated to show the mapping:
   # DAX: ...   -> the measure this reproduces
   # PQ:  ...    -> the Power Query step it resembles

Run after generating the CSVs. Reads from /mnt/user-data/outputs.
"""

import pandas as pd

BASE = "/mnt/user-data/outputs"

# ----------------------------------------------------------------------
# Load tables. parse_dates = let Pandas read timestamps as datetime,
# PQ: this is the automatic "Changed Type" step on date columns.
# ----------------------------------------------------------------------
work_orders = pd.read_csv(
    f"{BASE}/Fact_WorkOrders.csv",
    parse_dates=["FailureDate", "RepairStartDate", "RepairEndDate"],
)
production = pd.read_csv(f"{BASE}/Fact_Production.csv")
equipment  = pd.read_csv(f"{BASE}/Dim_Equipment.csv")

# IDs 2 = Corrective, 4 = Breakdown -> these count as "failures"
FAILURE_TYPES = [2, 4]


# ======================================================================
# HELPER COLUMNS / SERIES  (the base measures from the spec)
# ======================================================================

# Only unplanned events are failures.
# DAX: Failure Count = CALCULATE(COUNTROWS(...), TypeName IN {"Corrective","Breakdown"})
# PQ:  Filter Rows where MaintenanceTypeID in {2,4}
failures = work_orders[work_orders["MaintenanceTypeID"].isin(FAILURE_TYPES)].copy()

# Repair duration ONLY where both timestamps exist (the messy-data guard).
# DAX: Total Repair Hours = SUMX(FILTER(.., NOT ISBLANK(start) && NOT ISBLANK(end)), DATEDIFF(...))
# PQ:  add a custom column = Duration.TotalHours([RepairEndDate]-[RepairStartDate]), nulls drop out
both_ts = failures["RepairStartDate"].notna() & failures["RepairEndDate"].notna()
failures.loc[both_ts, "RepairHours"] = (
    (failures.loc[both_ts, "RepairEndDate"] - failures.loc[both_ts, "RepairStartDate"])
    .dt.total_seconds() / 3600.0
)


def kpis(failures_df, production_df):
    """Compute the fleet-level KPI set for a given slice of data.
    Passing a filtered slice = applying a Power BI page/visual filter."""

    # --- Failure / repair side ---
    failure_count = len(failures_df)                       # DAX: Failure Count
    total_repair_hours = failures_df["RepairHours"].sum()  # DAX: Total Repair Hours

    # DAX: MTTR (hrs) = DIVIDE([Total Repair Hours], [Failure Count])
    mttr = total_repair_hours / failure_count if failure_count else float("nan")

    # --- Production / time side (for MTBF + OEE) ---
    scheduled_hours = production_df["PlannedProductionMinutes"].sum() / 60      # DAX: Scheduled Hours
    total_downtime_hours = production_df["DowntimeMinutes"].sum() / 60          # DAX: Total Downtime Hours
    run_time_hours = scheduled_hours - total_downtime_hours                     # DAX: Run Time (hrs)

    # DAX: MTBF (hrs) = DIVIDE(Uptime, [Failure Count])
    mtbf = run_time_hours / failure_count if failure_count else float("nan")

    # --- OEE components ---
    # DAX: Availability = DIVIDE([Run Time],[Scheduled Hours])
    availability = run_time_hours / scheduled_hours

    # DAX: Performance = DIVIDE(SUMX(ideal*count), run_minutes)
    ideal_minutes = (production_df["IdealCycleTime"] * production_df["TotalCount"]).sum()
    run_minutes = (production_df["PlannedProductionMinutes"] - production_df["DowntimeMinutes"]).sum()
    performance = ideal_minutes / run_minutes

    # DAX: Quality = DIVIDE(SUM(GoodCount), SUM(TotalCount))
    quality = production_df["GoodCount"].sum() / production_df["TotalCount"].sum()

    oee = availability * performance * quality            # DAX: OEE = A * P * Q

    return {
        "Failures": failure_count,
        "MTBF (hrs)": round(mtbf, 1),
        "MTTR (hrs)": round(mttr, 2),
        "Availability": round(availability, 3),
        "Performance": round(performance, 3),
        "Quality": round(quality, 3),
        "OEE": round(oee, 3),
    }


# ======================================================================
# 1. FLEET-LEVEL  (one number across all equipment)
# ======================================================================
fleet = kpis(failures, production)
print("=== FLEET-LEVEL KPIs ===")
for k, v in fleet.items():
    print(f"  {k:14s} {v}")


# ======================================================================
# 2. ASSET-LEVEL  (per-equipment, then averaged)
# groupby == DAX iterating over VALUES(Dim_Equipment[EquipmentID]);
# PQ: this is "Group By" with aggregations.
# ======================================================================
rows = []
for equipment_id, prod_grp in production.groupby("EquipmentID"):
    fail_grp = failures[failures["EquipmentID"] == equipment_id]
    k = kpis(fail_grp, prod_grp)
    k["EquipmentID"] = equipment_id
    rows.append(k)

per_asset = pd.DataFrame(rows).merge(
    equipment[["EquipmentID", "AssetName", "CriticalityClass", "IsSeawaterCooled"]],
    on="EquipmentID",
)

# AVERAGEX equivalent: equal weight per asset (NOT weighted by activity)
print("\n=== ASSET-LEVEL (averaged equally across assets) ===")
print(f"  MTBF Asset Avg : {per_asset['MTBF (hrs)'].mean():.1f} hrs")
print(f"  MTTR Asset Avg : {per_asset['MTTR (hrs)'].mean():.2f} hrs")
print(f"  OEE  Asset Avg : {per_asset['OEE'].mean():.3f}")

# Worst performers by MTBF — the reliability deep-dive table
print("\n=== 5 LOWEST-MTBF ASSETS (reliability watchlist) ===")
watch = per_asset.sort_values("MTBF (hrs)").head(5)
print(watch[["AssetName", "CriticalityClass", "IsSeawaterCooled",
             "Failures", "MTBF (hrs)", "MTTR (hrs)", "OEE"]].to_string(index=False))

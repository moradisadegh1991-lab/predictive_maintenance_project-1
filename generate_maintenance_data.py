"""
Synthetic maintenance & production data generator for a Power BI portfolio.
Produces 7 CSVs matching the star schema (dims + 2 fact tables).

Designed to look like REAL petrochemical plant data:
- ~8% missing RepairEndDate (irregular / incomplete records)
- failure modes skewed (seawater leak boosted on seawater-cooled exchangers)
- Criticality A equipment fails less but costs more per event
- irregular (non-rounded) timestamps

Python notes for Power Query users are marked with  # PQ:
"""

import numpy as np
import pandas as pd

# A fixed seed makes the output reproducible (same data every run).
rng = np.random.default_rng(42)

START = pd.Timestamp("2024-01-01")
END   = pd.Timestamp("2025-12-31")

# ----------------------------------------------------------------------
# 1. Dim_Equipment
# ----------------------------------------------------------------------
asset_types = ["Centrifugal Pump", "Compressor", "Heat Exchanger",
               "Control Valve", "Electric Motor", "Steam Turbine"]
areas = ["Utility", "Process-A", "Process-B", "Cooling Water", "Compression"]

n_equipment = 36
equipment = pd.DataFrame({
    "EquipmentID": np.arange(1001, 1001 + n_equipment),
    # PQ: np.random.choice ≈ a custom column with a weighted random pick
    "AssetType": rng.choice(asset_types, n_equipment),
    "Area": rng.choice(areas, n_equipment),
    "CriticalityClass": rng.choice(["A", "B", "C"], n_equipment, p=[0.25, 0.45, 0.30]),
})
equipment["AssetName"] = equipment["AssetType"].str[:4].str.upper() + "-" + \
                         equipment["EquipmentID"].astype(str)
# Seawater-cooled = any heat exchanger OR anything in the Cooling Water area
# (in Asalouyeh / South Pars these run on seawater -> prone to seawater leak)
equipment["IsSeawaterCooled"] = (
    (equipment["AssetType"] == "Heat Exchanger") |
    (equipment["Area"] == "Cooling Water")
)
# Install date somewhere in the 8 years before the data window
equipment["InstallDate"] = START - pd.to_timedelta(
    rng.integers(365, 365 * 8, n_equipment), unit="D")

# ----------------------------------------------------------------------
# 2. Small reference dimensions
# ----------------------------------------------------------------------
dim_maint_type = pd.DataFrame({
    "MaintenanceTypeID": [1, 2, 3, 4],
    "TypeName": ["Preventive", "Corrective", "Predictive", "Breakdown"],
})
dim_failure_mode = pd.DataFrame({
    "FailureModeID": [1, 2, 3, 4, 5, 6],
    "FailureMode": ["Seal Leak", "Bearing Failure", "Seawater Leak",
                    "Corrosion", "Vibration", "Electrical Fault"],
})
dim_shift = pd.DataFrame({
    "ShiftID": [1, 2, 3],
    "ShiftName": ["Morning", "Evening", "Night"],
})

# ----------------------------------------------------------------------
# 3. Dim_Date  (marked date table in Power BI)
# ----------------------------------------------------------------------
dates = pd.date_range(START, END, freq="D")
dim_date = pd.DataFrame({"DateKey": dates.strftime("%Y%m%d").astype(int),
                         "Date": dates})
dim_date["Year"] = dates.year
dim_date["Month"] = dates.month
dim_date["MonthName"] = dates.strftime("%b")
dim_date["Quarter"] = "Q" + dates.quarter.astype(str)

# ----------------------------------------------------------------------
# 4. Fact_WorkOrders
# ----------------------------------------------------------------------
# Expected failures over 2 years scale inversely with criticality
# (A is well-maintained → fewer failures, but each event is costlier).
fail_rate = {"A": 3, "B": 6, "C": 10}

rows = []
wo_id = 50000
for _, eq in equipment.iterrows():
    n_failures = rng.poisson(fail_rate[eq["CriticalityClass"]])
    for _ in range(n_failures):
        # random failure timestamp (irregular minutes, not rounded)
        offset_min = rng.integers(0, int((END - START).total_seconds() // 60))
        failure_date = START + pd.to_timedelta(offset_min, unit="m")

        # 80% corrective, 20% breakdown for unplanned events
        m_type = rng.choice([2, 4], p=[0.8, 0.2])

        # failure-mode weights; boost seawater leak for seawater-cooled assets
        weights = np.array([0.20, 0.20, 0.05, 0.20, 0.20, 0.15])
        if eq["IsSeawaterCooled"]:
            weights = np.array([0.10, 0.10, 0.45, 0.20, 0.10, 0.05])
        f_mode = rng.choice(dim_failure_mode["FailureModeID"], p=weights)

        # repair starts a few hours after failure detected
        repair_start = failure_date + pd.to_timedelta(rng.integers(1, 12), unit="h")
        repair_hours = float(rng.gamma(shape=2.0, scale=4.0))  # right-skewed
        repair_end = repair_start + pd.to_timedelta(repair_hours, unit="h")

        # ~8% of records have a MISSING RepairEndDate (real-world data gap)
        if rng.random() < 0.08:
            repair_end = pd.NaT

        labor_hours = round(repair_hours * rng.uniform(0.8, 1.5), 1)
        cost_factor = {"A": 3.5, "B": 1.8, "C": 1.0}[eq["CriticalityClass"]]
        # both the labor component and the parts noise scale with criticality
        cost = round((labor_hours * 45 + rng.uniform(200, 3000)) * cost_factor, 2)

        rows.append([wo_id, eq["EquipmentID"], failure_date, repair_start,
                     repair_end, m_type, f_mode, labor_hours, cost])
        wo_id += 1

fact_wo = pd.DataFrame(rows, columns=[
    "WorkOrderID", "EquipmentID", "FailureDate", "RepairStartDate",
    "RepairEndDate", "MaintenanceTypeID", "FailureModeID", "LaborHours", "Cost"])

# ----------------------------------------------------------------------
# 5. Fact_Production  (grain: Equipment x Date x Shift)  -> drives OEE
# ----------------------------------------------------------------------
# Days that had an unplanned WO get higher downtime on that equipment.
wo_days = set(zip(fact_wo["EquipmentID"],
                  fact_wo["FailureDate"].dt.normalize()))

prod_rows = []
for eid in equipment["EquipmentID"]:
    for d in dates:
        for shift_id in dim_shift["ShiftID"]:
            planned = 480  # 8-hour shift in minutes
            base_down = rng.uniform(0, 25)              # routine minor stops
            event_down = 0
            if (eid, d) in wo_days:
                event_down = rng.uniform(60, 300)       # failure-driven downtime
            downtime = round(min(base_down + event_down, planned), 1)

            ideal_cycle = round(rng.uniform(0.4, 0.9), 3)   # min per unit
            run_minutes = planned - downtime
            # performance ~85-99% of theoretical throughput
            total_count = int((run_minutes / ideal_cycle) * rng.uniform(0.85, 0.99))
            good_count = int(total_count * rng.uniform(0.95, 0.999))  # quality

            prod_rows.append([eid, int(d.strftime("%Y%m%d")), shift_id,
                              planned, downtime, ideal_cycle, total_count, good_count])

fact_prod = pd.DataFrame(prod_rows, columns=[
    "EquipmentID", "DateKey", "ShiftID", "PlannedProductionMinutes",
    "DowntimeMinutes", "IdealCycleTime", "TotalCount", "GoodCount"])

# ----------------------------------------------------------------------
# 6. Export all tables to CSV
# ----------------------------------------------------------------------
tables = {
    "Dim_Equipment": equipment,
    "Dim_Date": dim_date,
    "Dim_MaintenanceType": dim_maint_type,
    "Dim_FailureMode": dim_failure_mode,
    "Dim_Shift": dim_shift,
    "Fact_WorkOrders": fact_wo,
    "Fact_Production": fact_prod,
}
for name, df in tables.items():
    df.to_csv(f"/mnt/user-data/outputs/{name}.csv", index=False)
    print(f"{name:22s} {len(df):>7,} rows")

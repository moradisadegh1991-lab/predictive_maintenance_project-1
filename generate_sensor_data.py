"""
Generate daily sensor readings with REALISTIC pre-failure degradation.

Idea (the physical story):
  - Each asset has baseline vibration / bearing temp / discharge pressure + noise.
  - In the ~21 days BEFORE each failure, a degradation ramp builds up:
        vibration rises, bearing temp rises, pressure drops.
  - After the failure (repair), readings return to baseline.
  - Mode-specific emphasis: leak modes hit pressure harder; bearing/vibration
    modes hit vibration/temp harder.

Output: Fact_SensorReadings.csv  (EquipmentID, Date, sensors...)
Units: SI -> vibration mm/s, temp degC, pressure bar.
"""

import numpy as np
import pandas as pd

rng = np.random.default_rng(7)
BASE = "/mnt/user-data/outputs"
RAMP_DAYS = 21

equipment = pd.read_csv(f"{BASE}/Dim_Equipment.csv")
work_orders = pd.read_csv(f"{BASE}/Fact_WorkOrders.csv", parse_dates=["FailureDate"])
failures = work_orders[work_orders["MaintenanceTypeID"].isin([2, 4])].copy()
failures["FailDay"] = failures["FailureDate"].dt.normalize()

dates = pd.date_range("2024-01-01", "2025-12-31", freq="D")
date_index = {d: i for i, d in enumerate(dates)}
n_days = len(dates)

# Failure modes: 1 Seal Leak, 2 Bearing, 3 Seawater Leak, 4 Corrosion,
#                5 Vibration, 6 Electrical
LEAK_MODES = {1, 3, 4}          # pressure-dominant precursors
MECH_MODES = {2, 5}             # vibration/temp-dominant precursors

rows = []
for _, eq in equipment.iterrows():
    eid = eq["EquipmentID"]

    # per-asset baselines (slightly different per machine)
    vib_base   = rng.uniform(1.8, 2.6)      # mm/s (healthy rotating equipment)
    temp_base  = rng.uniform(42, 50)        # degC bearing temp
    press_base = rng.uniform(5.5, 7.0)      # bar discharge pressure

    # start each sensor as baseline + gaussian noise over the whole period
    vib   = vib_base   + rng.normal(0, 0.20, n_days)
    temp  = temp_base  + rng.normal(0, 1.0,  n_days)
    press = press_base + rng.normal(0, 0.15, n_days)

    # add a degradation ramp before each failure of this asset
    eq_fail = failures[failures["EquipmentID"] == eid]
    for _, f in eq_fail.iterrows():
        d_idx = date_index.get(f["FailDay"])
        if d_idx is None:
            continue
        mode = f["FailureModeID"]
        # severity multipliers depending on failure mode
        v_sev = rng.uniform(4.0, 7.0) * (1.4 if mode in MECH_MODES else 0.8)
        t_sev = rng.uniform(12, 22)  * (1.3 if mode in MECH_MODES else 0.7)
        p_sev = rng.uniform(1.5, 3.0) * (1.5 if mode in LEAK_MODES else 0.4)

        start = max(0, d_idx - RAMP_DAYS)
        for t in range(start, d_idx):
            # progress 0->1 toward the failure; squared = accelerating decay
            progress = (t - start) / RAMP_DAYS
            ramp = progress ** 2
            vib[t]   = max(vib[t],   vib_base   + ramp * v_sev)
            temp[t]  = max(temp[t],  temp_base  + ramp * t_sev)
            press[t] = min(press[t], press_base - ramp * p_sev)

    for i, d in enumerate(dates):
        rows.append([eid, d, round(vib[i], 3), round(temp[i], 2), round(press[i], 3)])

sensors = pd.DataFrame(rows, columns=[
    "EquipmentID", "Date", "Vibration_mms", "BearingTemp_C", "Pressure_bar"])

# ~3% missing readings (real sensors drop out)
miss = rng.random(len(sensors)) < 0.03
sensors.loc[miss, ["Vibration_mms", "BearingTemp_C", "Pressure_bar"]] = np.nan

sensors.to_csv(f"{BASE}/Fact_SensorReadings.csv", index=False)
print(f"Fact_SensorReadings  {len(sensors):,} rows, {miss.mean():.1%} missing")

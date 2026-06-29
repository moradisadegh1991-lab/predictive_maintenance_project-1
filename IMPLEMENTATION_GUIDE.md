# Implementation Guide

A step-by-step guide to reproduce the full predictive maintenance analytics
pipeline — from raw CSVs to a scored watchlist and Power BI dashboard.

---

## Prerequisites

- **Python 3.9+** with `pandas`, `numpy`, `scikit-learn`
- **Power BI Desktop** (latest version recommended for sparkline support)
- ~50 MB of free disk space for the generated data

```bash
pip install pandas numpy scikit-learn
```

---

## Step 1 — Generate the core data

This creates the star-schema tables: 5 dimension tables and 2 fact tables.
The data models a 36-asset petrochemical fleet over 24 months (Jan 2024 –
Dec 2025), with realistic properties:

- **Criticality-weighted failure rates** — Class A assets fail less but cost
  3.5× more per event
- **Seawater-leak skew** — seawater-cooled equipment has 49% seawater-leak
  failures vs 7% for the rest
- **Messy data** — ~6% of repair records have a missing `RepairEndDate`

```bash
python generate_maintenance_data.py
```

**Output files:**

| File | Rows | Description |
|---|---|---|
| `Dim_Equipment.csv` | 36 | Assets with type, area, criticality, install date |
| `Dim_Date.csv` | 731 | Calendar table (mark as date table in Power BI) |
| `Dim_MaintenanceType.csv` | 4 | Preventive / Corrective / Predictive / Breakdown |
| `Dim_FailureMode.csv` | 6 | Seal leak through electrical fault |
| `Dim_Shift.csv` | 3 | Morning / Evening / Night |
| `Fact_WorkOrders.csv` | ~225 | One row per maintenance event |
| `Fact_Production.csv` | ~79K | Equipment × Date × Shift (OEE basis) |

---

## Step 2 — Generate sensor readings

Adds daily vibration (mm/s), bearing temperature (°C), and discharge pressure
(bar) for every asset. The key feature: **pre-failure degradation ramps**.

In the 21 days before each failure:
- Vibration rises (squared ramp — accelerating, like real bearing wear)
- Bearing temperature rises
- Pressure drops

After repair, readings return to baseline. ~3% of readings are missing (sensor
dropout). Failure-mode-specific emphasis: mechanical modes (bearing, vibration)
hit vibration/temp harder; leak modes hit pressure harder.

```bash
python generate_sensor_data.py
```

**Output:** `Fact_SensorReadings.csv` (~26K rows)

**Verification** — pick any failure event, plot vibration 24 days before it.
You should see a clear exponential ramp from ~2.5 mm/s baseline to 5–7 mm/s.

---

## Step 3 — Compute maintenance KPIs (Pandas)

Calculates the same MTBF / MTTR / OEE measures that the Power BI DAX computes,
so you can verify parity between the two implementations.

```bash
python maintenance_kpis_pandas.py
```

**Expected output (approximate):**

| KPI | Fleet value |
|---|---|
| OEE | 86.9% |
| MTBF (fleet) | ~2,725 h |
| MTTR (fleet) | ~7.2 h |
| Breakdown share | ~20% |

The script also produces an **asset-level** average (MTBF Asset Avg ~4,379 h)
and a reliability watchlist (5 lowest-MTBF assets). The difference between fleet
and asset-level numbers is instructive: fleet-level is pulled down by
high-failure assets; asset-level gives equal weight to every machine.

---

## Step 4 — Train the predictive model

This is the core ML step. Two models are trained and compared:

### 4a. Baseline model (no sensors)

```bash
python predictive_maintenance.py
```

Features: criticality, age, days since last failure, failure count (90d),
downtime (30d). Expected ROC-AUC ≈ **0.62**, failure recall ≈ **0.20**.

The model performs barely above random — because the synthetic failures are
Poisson-distributed in time, so there's no temporal signal to learn from
maintenance history alone.

### 4b. Full model (with sensor trends)

```bash
python predictive_maintenance_v2.py
```

Adds 7 sensor-trend features: vibration mean/max/slope, bearing temp mean/max,
pressure min/slope — all computed from the 30 days **before** each snapshot
(no data leakage).

**Expected results:**

| Model | ROC-AUC | Failure recall |
|---|---|---|
| Baseline (no sensors) | 0.64 | 0.22 |
| **+ sensor trends** | **0.84** | **0.51** |

**Top features** (by importance): `Vib_slope`, `Press_slope`, `Vib_max` —
the model learned that *trend* (how fast vibration is rising) predicts failure
better than the instantaneous level. This is the physical truth of bearing
degradation.

### Key design decisions

- **Time-based split** (train < June 2025, test ≥ June 2025) — not random.
  Random splits leak future information and inflate metrics.
- **class_weight="balanced"** — tells the model that the rare failure class
  matters. Without it, the model learns to predict "no failure" always (high
  accuracy, zero utility).
- **Threshold tuning** — the default 0.5 threshold prioritizes precision. For
  critical assets (Class A), lower it to 0.30–0.35 to catch more failures at
  the cost of more false alarms:
  ```python
  y_pred_tuned = (y_proba >= 0.35).astype(int)
  ```

---

## Step 5 — Score all assets and export predictions

```bash
python generate_predictions.py
```

Trains a RandomForest on all history before the latest snapshot, then scores
every asset's 30-day failure probability.

**Output:** `Predictions.csv` — one row per asset with:

| Column | Description |
|---|---|
| `EquipmentID` | FK to Dim_Equipment |
| `AssetName` | Human-readable name |
| `AssetType` | Pump, compressor, etc. |
| `CriticalityClass` | A / B / C |
| `IsSeawaterCooled` | Boolean |
| `Risk30d` | 0–100 (failure probability %) |
| `RiskBand` | Low (≤35) / Medium (36–55) / High (>55) |

In production, this script runs on a schedule (e.g., nightly cron) and Power BI
refreshes the CSV on its next data refresh cycle.

---

## Step 6 — Build the dashboard

### 6a. HTML dashboard (portable)

```bash
python generate_dashboard_data.py
# Then open dashboard.html in a browser
```

A single-page, self-contained report with all data embedded. No server needed.
Good for sharing with prospects or embedding in a portfolio site.

### 6b. Power BI dashboard

Open Power BI Desktop and follow these steps:

**Import data:**
Get Data → Text/CSV → import all 8 CSVs + `Predictions.csv`. Ensure date
columns are typed as `Date/Time` in Power Query.

**Apply theme:**
View → Themes → Browse → select `petrochemical_theme.json`. This sets the
control-room palette (dark background, teal/amber/red status colors).

**Build relationships** (Model view):

```
Dim_Equipment    →  Fact_WorkOrders     (EquipmentID)
Dim_Equipment    →  Fact_Production     (EquipmentID)
Dim_Equipment    →  Predictions         (EquipmentID)
Dim_Equipment    →  Fact_SensorReadings (EquipmentID)
Dim_MaintenanceType → Fact_WorkOrders   (MaintenanceTypeID)
Dim_FailureMode  →  Fact_WorkOrders     (FailureModeID)
Dim_Shift        →  Fact_Production     (ShiftID)
Dim_Date         →  Fact_Production     (DateKey)
Dim_Date         →  Fact_WorkOrders     (Date ↔ FailureDate)
Dim_Date         →  Fact_SensorReadings (Date)
```

Mark `Dim_Date` as the date table (right-click → Mark as date table → Date
column).

**Create measures** (in a `_Measures` table):

```dax
Failure Count =
CALCULATE(
    COUNTROWS( Fact_WorkOrders ),
    Dim_MaintenanceType[TypeName] IN { "Corrective", "Breakdown" }
)

Total Repair Hours =
SUMX(
    FILTER( Fact_WorkOrders,
        NOT ISBLANK( Fact_WorkOrders[RepairStartDate] )
        && NOT ISBLANK( Fact_WorkOrders[RepairEndDate] ) ),
    DATEDIFF( Fact_WorkOrders[RepairStartDate],
              Fact_WorkOrders[RepairEndDate], MINUTE ) / 60.0
)

Scheduled Hours =
DIVIDE( SUM( Fact_Production[PlannedProductionMinutes] ), 60 )

Total Downtime Hours =
DIVIDE( SUM( Fact_Production[DowntimeMinutes] ), 60 )

Run Time (hrs) = [Scheduled Hours] - [Total Downtime Hours]

MTBF (hrs) =
DIVIDE( [Run Time (hrs)], [Failure Count] )

MTTR (hrs) =
DIVIDE( [Total Repair Hours], [Failure Count] )

Availability =
DIVIDE( [Run Time (hrs)], [Scheduled Hours] )

Performance =
VAR IdealMinutes =
    SUMX( Fact_Production,
        Fact_Production[IdealCycleTime] * Fact_Production[TotalCount] )
VAR RunMinutes =
    SUM( Fact_Production[PlannedProductionMinutes] )
    - SUM( Fact_Production[DowntimeMinutes] )
RETURN DIVIDE( IdealMinutes, RunMinutes )

Quality =
DIVIDE( SUM( Fact_Production[GoodCount] ),
        SUM( Fact_Production[TotalCount] ) )

OEE = [Availability] * [Performance] * [Quality]

Breakdown Share =
DIVIDE(
    CALCULATE( COUNTROWS(Fact_WorkOrders),
        Dim_MaintenanceType[TypeName] = "Breakdown" ),
    [Failure Count]
)

Cumulative Failure % =
VAR ModesByCount =
    ADDCOLUMNS(
        ALLSELECTED( Dim_FailureMode[FailureMode] ),
        "@cnt", [Failure Count] )
VAR CurrentCnt = [Failure Count]
VAR AtOrAbove = FILTER( ModesByCount, [@cnt] >= CurrentCnt )
VAR RunningTotal = SUMX( AtOrAbove, [@cnt] )
VAR GrandTotal = SUMX( ModesByCount, [@cnt] )
RETURN DIVIDE( RunningTotal, GrandTotal )
```

**Build visuals** (Page 1 layout — 1280×720):

| Visual | Type | Data | Position |
|---|---|---|---|
| OEE card | Card | `[OEE]` (% format) | Top-left |
| MTBF card | Card | `[MTBF (hrs)]` (whole number) | Next to OEE |
| MTTR card | Card | `[MTTR (hrs)]` (1 decimal) | Next |
| Breakdown card | Card | `[Breakdown Share]` (%) | Next |
| Area slicer | Slicer | `Dim_Equipment[Area]` | Top-right |
| Seawater toggle | Slicer (tile style) | `Dim_Equipment[IsSeawaterCooled]` | Top-right |
| OEE trend | Line chart | Axis: Month, Values: OEE + A + P + Q | Middle-left (wide) |
| MTBF by crit. | Column chart | Axis: CriticalityClass, Value: MTBF | Middle-right |
| Failure Pareto | Combo (column + line) | Column: Failure Count, Line: Cumulative % | Bottom-left |
| Top downtime | Horizontal bar | Axis: AssetName, Value: Total Downtime Hours (Top 8) | Bottom-center |
| Watchlist | Table | AssetName, CriticalityClass, Risk30d (conditional formatting) | Bottom-right |

**Conditional formatting on the watchlist:**
Select the Risk30d column → Format → Conditional formatting → Background color →
Rules: ≥56 = red (#E5524A), 36–55 = amber (#E8A33D), ≤35 = green (#5FC9A0).

---

## Project structure

```
predictive-maintenance-analytics/
├── README.md                           # Project overview and case study
├── IMPLEMENTATION_GUIDE.md             # This file
├── generate_maintenance_data.py        # Step 1: core star-schema tables
├── generate_sensor_data.py             # Step 2: IoT sensor stream
├── maintenance_kpis_pandas.py          # Step 3: KPI parity check
├── predictive_maintenance.py           # Step 4a: baseline model
├── predictive_maintenance_v2.py        # Step 4b: sensor-enriched model
├── generate_predictions.py             # Step 5: live risk scoring
├── generate_dashboard_data.py          # Step 6a: HTML dashboard data
├── dashboard.html                      # Step 6a: portable dashboard
├── petrochemical_theme.json            # Step 6b: Power BI theme
├── PowerBI_Build_Guide.md              # Step 6b: detailed PBI instructions
├── maintenance_analytics_portfolio.md  # Data model + DAX reference
├── Dim_Equipment.csv                   # Generated dimension tables
├── Dim_Date.csv
├── Dim_MaintenanceType.csv
├── Dim_FailureMode.csv
├── Dim_Shift.csv
├── Fact_WorkOrders.csv                 # Generated fact tables
├── Fact_Production.csv
├── Fact_SensorReadings.csv
└── Predictions.csv                     # Model output
```

---

## Quick start

```bash
# Clone the repo
git clone https://github.com/[your-username]/predictive-maintenance-analytics.git
cd predictive-maintenance-analytics

# Generate everything
python generate_maintenance_data.py
python generate_sensor_data.py
python predictive_maintenance_v2.py
python generate_predictions.py
python generate_dashboard_data.py

# Open the HTML dashboard
open dashboard.html    # macOS
xdg-open dashboard.html  # Linux
start dashboard.html     # Windows
```

---

## Adapting to real plant data

This project is designed as a template. To use it with real CMMS/IoT data:

1. **Replace the CSVs** with exports from your CMMS (SAP PM, Maximo, FmWeb)
   and historian (OSIsoft PI, Wonderware). Keep the same column names or map
   them in Power Query.
2. **Adjust failure-mode weights** in the Pareto — your plant will have its own
   dominant modes.
3. **Add real sensor channels** — the feature engineering pattern
   (mean/max/slope over a lookback window) works for any numeric sensor:
   oil viscosity, motor current, flow rate, corrosion rate.
4. **Tune the prediction horizon** — 30 days is a starting point. Shorter
   windows (7–14 days) give more actionable alerts but need denser sensor data.
5. **Lower the decision threshold for Class A assets** — the cost of a missed
   critical failure far exceeds the cost of an extra inspection.

---

*Built by a maintenance data engineer with 10+ years in petrochemical
operations (South Pars / Asalouyeh). For questions, collaboration, or
contract inquiries: [your LinkedIn profile URL].*

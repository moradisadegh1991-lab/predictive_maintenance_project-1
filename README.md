# Predictive Maintenance & Reliability Analytics — Petrochemical Plant

An end-to-end reliability analytics project for rotating equipment in a
petrochemical plant (modelled on South Pars / Asalouyeh operations). It covers
the full path from a clean dimensional data model, through maintenance KPI logic
(MTBF / MTTR / OEE), to a machine-learning model that forecasts equipment
failure 30 days ahead — and a control-room dashboard that tells the story to
operations managers.

The headline result: enriching the model with **sensor-trend features lifts the
30-day failure forecast from near-random (ROC-AUC 0.64) to operationally useful
(0.84)**, more than doubling the share of failures caught in advance.

---

## 1. Business problem

Unplanned equipment failures in a petrochemical plant are expensive and unsafe.
Maintenance teams want to move from *reactive* repair toward *predictive*
intervention: knowing which asset is degrading before it stops. This project
demonstrates that shift on a realistic, messy dataset — incomplete repair
records, sensor noise, irregular timestamps — the conditions of real plant data.

The two questions it answers:

- **How reliable is the fleet right now?** — MTBF, MTTR, OEE, and where downtime
  concentrates.
- **What fails next?** — a 30-day failure-risk score per asset, ranked into a
  maintenance watchlist.

---

## 2. Data model

A classic star schema, designed so the two grains never collide:

- `Fact_WorkOrders` — one row per maintenance event (failure, repair times, cost,
  failure mode).
- `Fact_Production` — Equipment × Date × Shift, the basis for OEE.
- `Fact_SensorReadings` — daily vibration (mm/s), bearing temperature (°C) and
  discharge pressure (bar) per asset, with realistic pre-failure degradation.
- Dimensions: `Dim_Equipment`, `Dim_Date`, `Dim_MaintenanceType`,
  `Dim_FailureMode`, `Dim_Shift`.

Keeping work orders and production as separate fact tables (rather than a single
flattened table) is what keeps MTBF, MTTR and OEE all computable from one model
without double-counting.

---

## 3. KPI layer (MTBF / MTTR / OEE)

The same measures are implemented twice — once as DAX for Power BI, once as
Pandas — to keep the logic transparent and portable.

| KPI | Definition used | Fleet result |
|---|---|---|
| OEE | Availability × Performance × Quality | **86.9 %** |
| MTBF (fleet) | (Scheduled − downtime) hours ÷ failures | **~2,725 h** |
| MTTR (fleet) | Total repair hours ÷ failures | **~7.2 h** |
| Breakdown share | Unplanned breakdowns ÷ all failures | **~20 %** |

Both a **fleet-level** roll-up (one headline number) and an **asset-level**
average (equal weight per machine) are provided, because they answer different
management questions and can differ substantially.

A note on data hygiene that carries through the whole project: repair duration is
computed **only where both repair timestamps exist**, so the ~6 % of work orders
with a missing `RepairEndDate` do not silently distort MTTR.

---

## 4. Predictive maintenance

The problem is framed as supervised binary classification: *will this asset fail
within the next 30 days?* The core engineering work is building a leakage-free
snapshot table — one row per asset per month, where every feature is computed
only from data **before** the snapshot, and the label looks **forward** 30 days.
Train/test is split by time, not at random, to mirror real deployment.

### Why the first model failed — and what fixed it

A baseline model using only static and history features (criticality, age, days
since last failure, recent downtime) performed barely above chance. That was the
intended lesson: a model is only as good as the signal in its inputs.

Adding sensor-**trend** features — the slope and peak of vibration, bearing
temperature and pressure over the prior 30 days — gave the model the real
physical precursor of failure. Degradation is a *trend*, not a level, and the
feature importances confirm it: vibration slope and pressure slope dominate.

| Model | Features | ROC-AUC | Failure recall |
|---|---|---|---|
| Baseline | static + maintenance history | 0.64 | 0.22 |
| **+ sensor trends** | adds vibration / temp / pressure slope & peak | **0.84** | **0.51** |

Same algorithm (RandomForest with balanced class weights), same pipeline — only
better input signal. In production, the decision threshold would be lowered for
critical (Class A) assets, deliberately accepting more false alarms because a
missed critical failure costs far more than an extra inspection.

---

## 5. Dashboard

A single-page, control-room-style report aimed at operations managers and
maintenance supervisors. It opens with the reliability KPIs, states the predictive
thesis, then drills into OEE trend, failure-mode Pareto, MTBF by criticality, and
top downtime contributors — ending on the **predictive watchlist**: the assets
most likely to fail next, each with a live vibration trend rising toward its
warning and danger thresholds.

The Pareto surfaces the dominant failure mode in this fleet — **seawater leak** —
concentrated on seawater-cooled exchangers, exactly the kind of pattern a
reliability engineer would want flagged.

---

## 6. Tech stack

- **Power BI** — star schema, DAX measures, dashboard design (Report Server target)
- **DAX** — MTBF / MTTR / OEE and OEE components
- **Python** — Pandas / NumPy (data generation, KPI parity), scikit-learn
  (RandomForest failure model)
- **HTML / Chart.js** — the portable report layer in this repository

---

## 7. Repository contents

```
generate_maintenance_data.py     # synthetic CMMS + production data
generate_sensor_data.py          # daily sensors with pre-failure degradation
maintenance_kpis_pandas.py       # MTBF / MTTR / OEE in Pandas (mirrors the DAX)
predictive_maintenance.py        # baseline failure model
predictive_maintenance_v2.py     # + sensor features, before/after comparison
generate_dashboard_data.py       # aggregations + live risk scoring -> JSON
dashboard.html                   # the reliability dashboard
maintenance_analytics_portfolio.md  # data model + DAX reference
*.csv                            # generated star-schema tables
```

### Reproduce

```bash
python generate_maintenance_data.py     # 1. core tables
python generate_sensor_data.py          # 2. sensor stream (needs work orders)
python predictive_maintenance_v2.py     # 3. model + before/after metrics
python generate_dashboard_data.py       # 4. build dashboard data
# then open dashboard.html
```

---

## 8. Data realism

The dataset is synthetic but deliberately built to behave like real plant data:
missing repair timestamps, sensor noise and dropouts (~3 %), irregular event
times, failure rates that scale with asset criticality, costs weighted toward
critical assets, and seawater-leak failures concentrated on seawater-cooled
equipment. Units are SI throughout (°C, bar, mm/s, hours).

---

*Built as a demonstration of reliability and predictive-maintenance analytics for
industrial rotating equipment — data modelling, KPI engineering, machine
learning, and decision-ready reporting in one pipeline.*

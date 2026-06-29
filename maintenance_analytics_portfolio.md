# Reliability & Maintenance Analytics — Portfolio Dashboard Spec

A Power BI portfolio piece showcasing predictive/preventive maintenance KPIs
(MTBF, MTTR, OEE) for petrochemical plant equipment. Built on a clean star
schema, designed for synthetic but realistic data (missing values, irregular
timestamps, sensor noise).

---

## 1. Data Model (Star Schema)

### Fact tables

**`Fact_WorkOrders`** — grain: one row per maintenance work order / failure event
| Column | Type | Notes |
|---|---|---|
| WorkOrderID | int (PK) | |
| EquipmentID | int (FK → Dim_Equipment) | |
| FailureDate | datetime | when fault detected / WO opened |
| RepairStartDate | datetime | nullable (data quality) |
| RepairEndDate | datetime | nullable |
| MaintenanceTypeID | int (FK) | |
| FailureModeID | int (FK) | |
| LaborHours | decimal | |
| Cost | decimal | spare parts + labor |

**`Fact_Production`** — grain: EquipmentID × Date × Shift (needed for OEE)
| Column | Type | Notes |
|---|---|---|
| EquipmentID | int (FK) | |
| DateKey | int (FK → Dim_Date) | |
| ShiftID | int (FK → Dim_Shift) | |
| PlannedProductionMinutes | decimal | scheduled run time |
| DowntimeMinutes | decimal | unplanned stop |
| TotalCount | decimal | units / kg/h produced |
| GoodCount | decimal | within spec |
| IdealCycleTime | decimal | min per unit (theoretical) |

### Dimension tables

- **`Dim_Equipment`**: EquipmentID, AssetName, AssetType (pump/compressor/heat exchanger…), Area, Unit, CriticalityClass (A/B/C), InstallDate, ParentAssetID
- **`Dim_Date`**: standard marked date table
- **`Dim_MaintenanceType`**: Preventive, Corrective, Predictive, Breakdown
- **`Dim_FailureMode`**: Seal leak, Bearing failure, **Seawater leak**, Corrosion, Vibration, Electrical…
- **`Dim_Shift`**: Morning / Evening / Night

> Relationships: all dims → facts, single-direction, 1-to-many.
> `Dim_Date` connects to both `FailureDate` (active) and `Fact_Production[DateKey]`.

---

## 2. Core DAX Measures

### Base / helper measures

```dax
Failure Count =
CALCULATE(
    COUNTROWS( Fact_WorkOrders ),
    Dim_MaintenanceType[TypeName] IN { "Corrective", "Breakdown" }
)

-- Repair duration only where BOTH timestamps exist (handles messy/null data)
Total Repair Hours =
SUMX(
    FILTER(
        Fact_WorkOrders,
        NOT ISBLANK( Fact_WorkOrders[RepairStartDate] )
            && NOT ISBLANK( Fact_WorkOrders[RepairEndDate] )
    ),
    DATEDIFF( Fact_WorkOrders[RepairStartDate],
              Fact_WorkOrders[RepairEndDate], MINUTE ) / 60.0
)

Total Downtime Hours =
DIVIDE( SUM( Fact_Production[DowntimeMinutes] ), 60 )

-- Scheduled operating hours in the current filter context
Scheduled Hours =
DIVIDE( SUM( Fact_Production[PlannedProductionMinutes] ), 60 )
```

### MTTR — Mean Time To Repair

```dax
MTTR (hrs) =
DIVIDE( [Total Repair Hours], [Failure Count] )
```

### MTBF — Mean Time Between Failures

```dax
-- Uptime-based definition: (scheduled time − downtime) / number of failures
MTBF (hrs) =
VAR Uptime = [Scheduled Hours] - [Total Downtime Hours]
RETURN
    DIVIDE( Uptime, [Failure Count] )
```

> Note: if you prefer the simpler "scheduled time / failures" definition,
> swap `Uptime` for `[Scheduled Hours]`. State the assumption on the dashboard.

### Availability (A) — for OEE

```dax
Run Time (hrs) =
[Scheduled Hours] - [Total Downtime Hours]

Availability =
DIVIDE( [Run Time (hrs)], [Scheduled Hours] )
```

### Performance (P)

```dax
Performance =
VAR IdealMinutes =
    SUMX( Fact_Production,
          Fact_Production[IdealCycleTime] * Fact_Production[TotalCount] )
VAR RunMinutes =
    SUM( Fact_Production[PlannedProductionMinutes] )
      - SUM( Fact_Production[DowntimeMinutes] )
RETURN
    DIVIDE( IdealMinutes, RunMinutes )
```

### Quality (Q)

```dax
Quality =
DIVIDE( SUM( Fact_Production[GoodCount] ),
        SUM( Fact_Production[TotalCount] ) )
```

### OEE

```dax
OEE =
[Availability] * [Performance] * [Quality]
```

### Trend / reliability extras

```dax
-- Availability vs. prior month (for KPI cards with trend arrows)
Availability MoM =
VAR Curr = [Availability]
VAR Prev = CALCULATE( [Availability], DATEADD( Dim_Date[Date], -1, MONTH ) )
RETURN Curr - Prev

-- % of work orders that are reactive (lower = more mature maintenance)
Reactive Ratio =
DIVIDE(
    [Failure Count],
    CALCULATE( COUNTROWS( Fact_WorkOrders ) )
)
```

---

## 3. Report Pages (UX / storytelling)

**Page 1 — Executive Overview** (audience: operations managers)
- KPI cards: OEE, MTBF, MTTR, Reactive Ratio (with MoM trend arrows)
- OEE trend line (12 months) with A/P/Q breakdown
- Top 5 equipment by downtime (bar)

**Page 2 — Reliability Deep-Dive** (maintenance supervisors)
- MTBF/MTTR by AssetType and CriticalityClass (matrix)
- Failure mode Pareto (highlight Seawater leak / Corrosion)
- Equipment drill-through

**Page 3 — Predictive signals** (your differentiator)
- Failure frequency vs. time-since-last-maintenance (scatter)
- Equipment trending toward MTBF breach (conditional formatting)

---

## 4. Synthetic Data Tips (to look realistic)

- Inject ~5–10% null `RepairEndDate` so your DAX null-handling is visible.
- Skew failure modes (seawater leak common on seawater-cooled exchangers).
- Make CriticalityClass A equipment fail less but cost more per event.
- Irregular timestamps (not all on the hour) to show real-world handling.

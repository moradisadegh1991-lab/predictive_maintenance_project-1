"""Compute every aggregation the dashboard needs + live predictive risk per asset.
Writes /home/claude/dashboard_data.json (embedded into the HTML afterwards)."""
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, recall_score

BASE = "/mnt/user-data/outputs"
FAIL = [2, 4]

wo = pd.read_csv(f"{BASE}/Fact_WorkOrders.csv", parse_dates=["FailureDate", "RepairStartDate", "RepairEndDate"])
prod = pd.read_csv(f"{BASE}/Fact_Production.csv")
eq = pd.read_csv(f"{BASE}/Dim_Equipment.csv", parse_dates=["InstallDate"])
fm = pd.read_csv(f"{BASE}/Dim_FailureMode.csv")
sensors = pd.read_csv(f"{BASE}/Fact_SensorReadings.csv", parse_dates=["Date"])

prod["Date"] = pd.to_datetime(prod["DateKey"], format="%Y%m%d")
prod["Month"] = prod["Date"].dt.to_period("M").astype(str)
failures = wo[wo.MaintenanceTypeID.isin(FAIL)]

def kpis(f, p):
    fc = len(f)
    both = f["RepairStartDate"].notna() & f["RepairEndDate"].notna()
    rep_h = ((f.loc[both, "RepairEndDate"] - f.loc[both, "RepairStartDate"]).dt.total_seconds()/3600).sum()
    sched = p["PlannedProductionMinutes"].sum()/60
    down = p["DowntimeMinutes"].sum()/60
    run = sched - down
    ideal = (p["IdealCycleTime"]*p["TotalCount"]).sum()
    runmin = (p["PlannedProductionMinutes"]-p["DowntimeMinutes"]).sum()
    a = run/sched; pf = ideal/runmin; q = p["GoodCount"].sum()/p["TotalCount"].sum()
    return dict(failures=fc, mtbf=run/fc, mttr=rep_h/fc, availability=a,
                performance=pf, quality=q, oee=a*pf*q)

fleet = kpis(failures, prod)
breakdown_share = (failures.MaintenanceTypeID==4).mean()

# OEE trend by month
oee_rows = []
for m, g in prod.groupby("Month"):
    a = 1 - g["DowntimeMinutes"].sum()/g["PlannedProductionMinutes"].sum()
    pf = (g["IdealCycleTime"]*g["TotalCount"]).sum()/(g["PlannedProductionMinutes"]-g["DowntimeMinutes"]).sum()
    q = g["GoodCount"].sum()/g["TotalCount"].sum()
    oee_rows.append(dict(month=m, availability=round(a,3), performance=round(pf,3),
                         quality=round(q,3), oee=round(a*pf*q,3)))

# Failure-mode Pareto
fmode = (failures.merge(fm).groupby("FailureMode").size().sort_values(ascending=False))
cum = (fmode.cumsum()/fmode.sum()*100).round(1)
pareto = [dict(mode=k, count=int(v), cum=float(cum[k])) for k, v in fmode.items()]

# MTBF by criticality
crit_rows = []
for c in ["A", "B", "C"]:
    ids = eq.loc[eq.CriticalityClass==c, "EquipmentID"]
    k = kpis(failures[failures.EquipmentID.isin(ids)], prod[prod.EquipmentID.isin(ids)])
    crit_rows.append(dict(crit=c, mtbf=round(k["mtbf"],0), mttr=round(k["mttr"],1), failures=k["failures"]))

# Top equipment by downtime
dt = (prod.groupby("EquipmentID")["DowntimeMinutes"].sum()/60).sort_values(ascending=False).head(8)
dt = dt.reset_index().merge(eq[["EquipmentID","AssetName","CriticalityClass"]])
top_down = [dict(name=r.AssetName, hours=round(r.DowntimeMinutes,0), crit=r.CriticalityClass)
            for r in dt.itertuples()]

# ---- Predictive risk (train on history, score the latest snapshot per asset) ----
daily_dt = prod.groupby(["EquipmentID","Date"])["DowntimeMinutes"].sum().reset_index()
sens_by = {i:g.sort_values("Date") for i,g in sensors.groupby("EquipmentID")}
def slope(v):
    v = pd.Series(v).dropna()
    return float(np.polyfit(np.arange(len(v)), v.values, 1)[0]) if len(v)>1 else 0.0

snaps = pd.date_range("2024-02-01","2025-11-01",freq="MS")
rows = []
for _, e in eq.iterrows():
    eid=e.EquipmentID; ef=failures.loc[failures.EquipmentID==eid,"FailureDate"].sort_values()
    ed=daily_dt[daily_dt.EquipmentID==eid]; es=sens_by[eid]
    for s in snaps:
        he=s+pd.Timedelta(days=30); lab=int(((ef>=s)&(ef<he)).any()); past=ef[ef<s]
        m30=(ed.Date>=s-pd.Timedelta(days=30))&(ed.Date<s)
        sw=es[(es.Date>=s-pd.Timedelta(days=30))&(es.Date<s)]
        rows.append(dict(EquipmentID=eid, Snap=s,
            CriticalityRank={"A":0,"B":1,"C":2}[e.CriticalityClass],
            IsSeawaterCooled=int(e.IsSeawaterCooled),
            AgeYears=(s-e.InstallDate).days/365,
            DaysSinceLastFailure=(s-past.max()).days if len(past) else 9999,
            FailuresPrev90d=int(((past>=s-pd.Timedelta(days=90))&(past<s)).sum()),
            DowntimePrev30d=ed.loc[m30,"DowntimeMinutes"].sum(),
            Vib_mean=sw.Vibration_mms.mean(), Vib_max=sw.Vibration_mms.max(), Vib_slope=slope(sw.Vibration_mms),
            Temp_mean=sw.BearingTemp_C.mean(), Temp_max=sw.BearingTemp_C.max(),
            Press_min=sw.Pressure_bar.min(), Press_slope=slope(sw.Pressure_bar), y=lab))
d = pd.DataFrame(rows)
num = d.select_dtypes(include="number").columns
d[num] = d[num].fillna(d[num].median())
FEAT=["CriticalityRank","IsSeawaterCooled","AgeYears","DaysSinceLastFailure","FailuresPrev90d",
      "DowntimePrev30d","Vib_mean","Vib_max","Vib_slope","Temp_mean","Temp_max","Press_min","Press_slope"]
BASEF=["CriticalityRank","IsSeawaterCooled","AgeYears","DaysSinceLastFailure","FailuresPrev90d","DowntimePrev30d"]

latest = d.Snap==d.Snap.max()
tr = d.Snap<d.Snap.max()
def fit_auc(cols):
    m=RandomForestClassifier(n_estimators=300,max_depth=6,class_weight="balanced",random_state=42)
    # eval on a held-out time split for the headline numbers
    ev_tr=d.Snap<pd.Timestamp("2025-06-01"); ev_te=(~ev_tr)
    m.fit(d.loc[ev_tr,cols], d.loc[ev_tr,"y"])
    pr=m.predict_proba(d.loc[ev_te,cols])[:,1]
    return roc_auc_score(d.loc[ev_te,"y"],pr), recall_score(d.loc[ev_te,"y"],(pr>=0.5).astype(int))
auc_base,rec_base = fit_auc(BASEF)
auc_full,rec_full = fit_auc(FEAT)

# live scoring model trained on everything before the last snapshot
live=RandomForestClassifier(n_estimators=300,max_depth=6,class_weight="balanced",random_state=42)
live.fit(d.loc[tr,FEAT], d.loc[tr,"y"])
d_latest=d[latest].copy()
d_latest["risk"]=live.predict_proba(d_latest[FEAT])[:,1]
d_latest=d_latest.merge(eq[["EquipmentID","AssetName","AssetType","CriticalityClass","IsSeawaterCooled"]])
watch=d_latest.sort_values("risk",ascending=False).head(6)

watchlist=[]
for r in watch.itertuples():
    es=sens_by[r.EquipmentID]
    spark=es[es.Date<d.Snap.max()].tail(30)["Vibration_mms"].round(2)
    spark=spark.ffill().bfill().tolist()
    watchlist.append(dict(name=r.AssetName, type=r.AssetType, crit=r.CriticalityClass,
        seawater=bool(r.IsSeawaterCooled), risk=round(float(r.risk)*100), vib=spark))

DATA=dict(
    plant="Kavian Petrochemical — Asalouyeh / South Pars",
    period="Jan 2024 – Dec 2025",
    assets=int(len(eq)),
    fleet={k:round(v,3) for k,v in fleet.items()},
    breakdown_share=round(breakdown_share,3),
    oee_trend=oee_rows, pareto=pareto, by_crit=crit_rows, top_down=top_down,
    model=dict(auc_base=round(auc_base,3), auc_full=round(auc_full,3),
               rec_base=round(rec_base,2), rec_full=round(rec_full,2)),
    watchlist=watchlist,
    vib_danger=7.1, vib_warn=4.5)

json.dump(DATA, open("/home/claude/dashboard_data.json","w"), indent=1)
print("OEE:",DATA["fleet"]["oee"],"MTBF:",round(DATA["fleet"]["mtbf"]),
      "AUC base->full:",DATA["model"]["auc_base"],"->",DATA["model"]["auc_full"],
      "| watchlist:",len(watchlist))

"""Score all assets' latest snapshot and export Predictions.csv.
Trains on all history before the last month, then scores every asset's
30-day failure probability for the Power BI watchlist."""
import numpy as np, pandas as pd
from sklearn.ensemble import RandomForestClassifier

BASE, FAIL = ".", [2, 4]
wo = pd.read_csv(f"{BASE}/Fact_WorkOrders.csv", parse_dates=["FailureDate"])
prod = pd.read_csv(f"{BASE}/Fact_Production.csv")
eq = pd.read_csv(f"{BASE}/Dim_Equipment.csv", parse_dates=["InstallDate"])
sensors = pd.read_csv(f"{BASE}/Fact_SensorReadings.csv", parse_dates=["Date"])
prod["Date"] = pd.to_datetime(prod["DateKey"], format="%Y%m%d")
failures = wo[wo.MaintenanceTypeID.isin(FAIL)]
daily_dt = prod.groupby(["EquipmentID","Date"])["DowntimeMinutes"].sum().reset_index()
sens_by = {i:g.sort_values("Date") for i,g in sensors.groupby("EquipmentID")}

def slope(v):
    v = pd.Series(v).dropna()
    return float(np.polyfit(np.arange(len(v)), v.values, 1)[0]) if len(v)>1 else 0.0

snaps = pd.date_range("2024-02-01","2025-11-01",freq="MS")
rows=[]
for _, e in eq.iterrows():
    eid=e.EquipmentID; ef=failures.loc[failures.EquipmentID==eid,"FailureDate"].sort_values()
    ed=daily_dt[daily_dt.EquipmentID==eid]; es=sens_by[eid]
    for s in snaps:
        he=s+pd.Timedelta(days=30); past=ef[ef<s]
        m30=(ed.Date>=s-pd.Timedelta(days=30))&(ed.Date<s)
        sw=es[(es.Date>=s-pd.Timedelta(days=30))&(es.Date<s)]
        rows.append(dict(EquipmentID=eid, Snap=s,
            CriticalityRank={"A":0,"B":1,"C":2}[e.CriticalityClass],
            IsSeawaterCooled=int(e.IsSeawaterCooled), AgeYears=(s-e.InstallDate).days/365,
            DaysSinceLastFailure=(s-past.max()).days if len(past) else 9999,
            FailuresPrev90d=int(((past>=s-pd.Timedelta(days=90))&(past<s)).sum()),
            DowntimePrev30d=ed.loc[m30,"DowntimeMinutes"].sum(),
            Vib_mean=sw.Vibration_mms.mean(), Vib_max=sw.Vibration_mms.max(), Vib_slope=slope(sw.Vibration_mms),
            Temp_mean=sw.BearingTemp_C.mean(), Temp_max=sw.BearingTemp_C.max(),
            Press_min=sw.Pressure_bar.min(), Press_slope=slope(sw.Pressure_bar),
            y=int(((ef>=s)&(ef<he)).any())))
d=pd.DataFrame(rows); num=d.select_dtypes(include="number").columns
d[num]=d[num].fillna(d[num].median())
FEAT=["CriticalityRank","IsSeawaterCooled","AgeYears","DaysSinceLastFailure","FailuresPrev90d",
      "DowntimePrev30d","Vib_mean","Vib_max","Vib_slope","Temp_mean","Temp_max","Press_min","Press_slope"]
tr=d.Snap<d.Snap.max(); latest=d.Snap==d.Snap.max()
m=RandomForestClassifier(n_estimators=300,max_depth=6,class_weight="balanced",random_state=42)
m.fit(d.loc[tr,FEAT], d.loc[tr,"y"])
out=d[latest].copy(); out["Risk30d"]=(m.predict_proba(out[FEAT])[:,1]*100).round(0).astype(int)
out=out.merge(eq[["EquipmentID","AssetName","AssetType","CriticalityClass","IsSeawaterCooled"]])
out["RiskBand"]=pd.cut(out["Risk30d"],[-1,35,55,200],labels=["Low","Medium","High"])
pred=out[["EquipmentID","AssetName","AssetType","CriticalityClass","IsSeawaterCooled","Risk30d","RiskBand"]]\
      .sort_values("Risk30d",ascending=False)
pred.to_csv(f"{BASE}/Predictions.csv",index=False)
print(pred.head(8).to_string(index=False))
print(f"\nWrote Predictions.csv ({len(pred)} assets)")

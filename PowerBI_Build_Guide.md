# راهنمای ساخت داشبورد در Power BI

این راهنما همان داشبورد reliability/predictive را که به‌صورت HTML دیدید، گام‌به‌گام در Power BI Desktop (و سپس قابل publish روی Report Server) بازسازی می‌کند. از همان CSVها، measureهای DAX، و فایل theme استفاده می‌شود.

پیش‌نیاز فایل‌ها: همه‌ی `Dim_*.csv` و `Fact_*.csv`، فایل `Predictions.csv`، و `petrochemical_theme.json`.

---

## گام ۰ — اعمال theme

`View → Themes → Browse for themes` → فایل `petrochemical_theme.json` را انتخاب کنید. این پالت اتاق-کنترل (پس‌زمینه‌ی نفتی-تیره، فیروزه‌ای/کهربایی/قرمز) را روی کل گزارش اعمال می‌کند، تا رنگ‌ها را دستی تنظیم نکنید.

---

## گام ۱ — import داده‌ها

`Get Data → Text/CSV` (یا `Folder` اگر همه در یک پوشه‌اند). هر هشت جدول مدل + `Predictions` را وارد کنید. در Power Query این نکات را چک کنید:

- نوع ستون‌های تاریخ (`FailureDate`, `RepairStartDate`, `RepairEndDate`, `Date`) را `Date/Time` کنید.
- `RepairEndDate` را همان‌طور با مقادیر null رها کنید — منطق DAX خودش هندل می‌کند.
- `DateKey` در `Fact_Production` به‌صورت Whole Number بماند (کلید ارتباط با Dim_Date).

---

## گام ۲ — مدل ستاره‌ای (Relationships)

در نمای Model این ارتباط‌ها را بسازید (همه single-direction، 1-to-many از dim به fact):

| از (dimension) | به (fact) | ستون |
|---|---|---|
| Dim_Equipment | Fact_WorkOrders | EquipmentID |
| Dim_Equipment | Fact_Production | EquipmentID |
| Dim_Equipment | Predictions | EquipmentID |
| Dim_MaintenanceType | Fact_WorkOrders | MaintenanceTypeID |
| Dim_FailureMode | Fact_WorkOrders | FailureModeID |
| Dim_Shift | Fact_Production | ShiftID |
| Dim_Date | Fact_Production | DateKey ↔ DateKey |
| Dim_Date | Fact_WorkOrders | Date ↔ FailureDate (active) |

سپس `Dim_Date` را به‌عنوان date table علامت بزنید: روی جدول کلیک راست → `Mark as date table` → ستون `Date`.

نکته‌ی sensor: `Fact_SensorReadings` کلید ترکیبی (EquipmentID + Date) دارد. ساده‌ترین راه: ارتباط `Dim_Equipment[EquipmentID] → Fact_SensorReadings[EquipmentID]` و یک ارتباط `Dim_Date[Date] → Fact_SensorReadings[Date]`. برای visualهای sensor از همین دو dimension به‌عنوان axis استفاده کنید.

---

## گام ۳ — measureهای DAX

یک جدول خالی برای measureها بسازید (`Enter Data` → نام `_Measures`). measureهای پایه و KPI را از فایل `maintenance_analytics_portfolio.md` کپی کنید. این‌ها حداقل مجموعه‌ای است که این داشبورد لازم دارد:

پایه: `Failure Count`, `Total Repair Hours`, `Scheduled Hours`, `Total Downtime Hours`, `Run Time (hrs)`.

KPI: `MTTR (hrs)`, `MTBF (hrs)`, `Availability`, `Performance`, `Quality`, `OEE`.

دو measure اضافه که این داشبورد لازم دارد:

```dax
Breakdown Share =
DIVIDE(
    CALCULATE( COUNTROWS(Fact_WorkOrders), Dim_MaintenanceType[TypeName] = "Breakdown" ),
    [Failure Count]
)
```

برای نمودار Pareto، درصد تجمعی لازم است:

```dax
Cumulative Failure % =
VAR ModesByCount =
    ADDCOLUMNS(
        ALLSELECTED( Dim_FailureMode[FailureMode] ),
        "@cnt", [Failure Count]
    )
VAR CurrentCnt = [Failure Count]
VAR AtOrAbove =
    FILTER( ModesByCount, [@cnt] >= CurrentCnt )
VAR RunningTotal =
    SUMX( AtOrAbove, [@cnt] )
VAR GrandTotal =
    SUMX( ModesByCount, [@cnt] )
RETURN
    DIVIDE( RunningTotal, GrandTotal )
```

برای فلش روند روی KPI cardها (اختیاری):

```dax
OEE MoM =
[OEE] - CALCULATE( [OEE], DATEADD( Dim_Date[Date], -1, MONTH ) )
```

---

## گام ۴ — صفحه‌ی گزارش (visualها)

اندازه‌ی canvas را `16:9` نگه دارید. چیدمان از بالا به پایین، مطابق نسخه‌ی HTML:

### ردیف بالا — چهار KPI card
چهار visual نوع `Card` کنار هم:

- `OEE` (فرمت Percentage) — با conditional formatting: سبز اگر ≥ ۰.۸۵، کهربایی ۰.۷–۰.۸۵، قرمز < ۰.۷.
- `MTBF (hrs)` — فرمت Whole Number با هزارگان.
- `MTTR (hrs)` — یک رقم اعشار.
- `Breakdown Share` — Percentage.

اگر می‌خواهید عددها readout-مانند (monospace) دیده شوند، در فرمت فونت card از `DIN` یا `Consolas` استفاده کنید.

### OEE & components — Line chart
`Line chart`؛ Axis = `Dim_Date[Month]` (یا یک ستون Year-Month مرتب)، Values = `OEE`, `Availability`, `Performance`, `Quality`. خط OEE را ضخیم‌تر و فیروزه‌ای؛ بقیه را نازک و dim کنید. محور Y را روی ۷۵٪–۱۰۰٪ ببندید تا تغییرات دیده شود.

### Failure modes — Pareto
`Line and stacked column chart`:
- X axis = `Dim_FailureMode[FailureMode]` (مرتب بر اساس Failure Count نزولی)
- Column = `[Failure Count]`
- Line = `[Cumulative Failure %]` (محور ثانویه، ۰–۱۰۰٪)
- ستون `Seawater Leak` را با data color جداگانه فیروزه‌ای پررنگ کنید تا الگوی آب دریا برجسته شود.

### MTBF by criticality — Bar
`Clustered column`؛ Axis = `Dim_Equipment[CriticalityClass]`، Value = `[MTBF (hrs)]`. رنگ ستون‌ها را با rule بزنید: A=قرمز، B=کهربایی، C=فیروزه‌ای.

### Top equipment by downtime — Horizontal bar
`Clustered bar chart`؛ Axis = `Dim_Equipment[AssetName]`، Value = `[Total Downtime Hours]`. یک `Top N` filter (Top 8 بر اساس همان measure) روی visual بگذارید.

### Predictive watchlist — جدول با sparkline (عنصر امضا)
یک `Table`:
- ستون‌ها: `AssetName`, `CriticalityClass`, `Risk30d` (از جدول `Predictions`).
- `Risk30d` را با data bar یا background conditional formatting رنگ کنید (High=قرمز، Medium=کهربایی، Low=سبز) — یا از ستون `RiskBand` برای آیکن استفاده کنید.
- مرتب‌سازی نزولی بر اساس `Risk30d`، و یک `Top N` (مثلاً Top 6).

برای sparkline روند vibration در همان جدول: Power BI نسخه‌های جدید قابلیت **Sparklines** را در table/matrix دارد (`Add a sparkline` روی یک ستون عددی). یک measure ساده بسازید:

```dax
Vibration (avg) = AVERAGE( Fact_SensorReadings[Vibration_mms] )
```

سپس روی جدول، sparkline اضافه کنید با Y = `[Vibration (avg)]` و axis = `Dim_Date[Date]`، و بازه‌ی تاریخ را با یک فیلتر relative به آخرین ۳۰ روز محدود کنید. برای خط آستانه‌ی خطر، در فرمت sparkline یک constant line روی ۷.۱ بگذارید (اگر نسخه‌تان پشتیبانی می‌کند)؛ در غیر این صورت به‌صورت یک measure مرجع نشانش دهید.

---

## گام ۵ — تعامل و انتشار

- یک `Slicer` روی `Dim_Equipment[Area]` و یکی روی `IsSeawaterCooled` اضافه کنید تا مدیر بتواند fleet را فیلتر کند (همان slicerهایی که در طراحی HTML اشاره کردیم).
- یک slicer بازه‌ی تاریخ روی `Dim_Date`.
- `File → Publish` به Power BI Service، یا save به `.pbix` و آپلود به Report Server محیط خودتان.

---

## نکته‌ی مهم درباره‌ی امتیاز ریسک

ستون `Risk30d` در `Predictions.csv` خروجی **مدل Python (RandomForest)** است، نه محاسبه‌ی داخل Power BI. گردش‌کار واقعی این است: مدل به‌صورت دوره‌ای (مثلاً شبانه) اجرا می‌شود، `Predictions.csv` را به‌روز می‌کند، و Power BI با refresh آن را می‌خواند. اگر خواستید scoring را داخل خود Power BI ببرید، می‌توان از Python visual یا یک Power Query با اجرای اسکریپت استفاده کرد — ولی الگوی export/import پایدارتر و سریع‌تر است و برای Report Server توصیه می‌شود.

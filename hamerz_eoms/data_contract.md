# منشور جامع داده‌ها، معماری و چک‌لیست پایداری (EOMS Data Contract v1.4.3)
**پروژه:** یکپارچه‌سازی فرآیندهای چابک Linear در ساختار Frappe/ERPNext v16  
**سازمان:** شرکت سولار الکتریک انرژی / هامرز پلاسما  
**نسخه:** Baseline v1.4.3 (Scaffolded Frappe App Package)

---

## ۱. ساختار داک‌تایپ‌های استاندارد و سفارشی اپلیکیشن `hamerz_eoms`

این پکیج به صورت یک اپلیکیشن استاندارد فِرپ ۱۶ همراه با `pyproject.toml`، `modules.txt`، ساختار `doctype` و فیکسچرهای `fixtures/custom_field.json` پیاده‌سازی شده است.

### ۱.۱. فیلدهای سفارشی تزریق‌شده به داک‌تایپ استاندارد `Task`
| نام فیلد (Fieldname) | نوع (Fieldtype) | گزینه‌ها / مرجع (Options) | دسترسی و قواعد سیستمی |
| :--- | :--- | :--- | :--- |
| `custom_team` | Link | `Work Team` | متولی تیمی تسک؛ الزامی برای رول‌اور |
| `custom_cycle` | Link | `Work Cycle` | سایکل فعال جاری |
| `custom_task_lead` | Link | `User` | مسئول پاسخگوی تسک؛ گیرنده اصلی اعلان‌ها |
| `custom_state_category` | Select | `Triage\nBacklog\nUnstarted\nStarted\nCompleted\nCancelled` | منبع اصلی وضعیت کار چابک |
| `custom_story_points` | Float | پیش‌فرض: ۰ | برآورد وزنی برای طعم‌های A و B |
| `custom_machine_hours` | Float | پیش‌فرض: ۰ | برآورد وزنی برای طعم C (قالب‌سازی) |
| `custom_rollover_count` | Int | پیش‌فرض: ۰ (فقط‌خواندنی) | شمارنده سرریز میان سایکل‌ها |
| `custom_is_blocked` | Check | پیش‌فرض: ۰ | تعلیق افزایش شمارنده در صورت انسداد |
| `custom_needs_review` | Check | پیش‌فرض: ۰ | پرچم هشدار نیازمند بررسی لید/کیفیت |
| `custom_reopen_reason` | Small Text | - | اجباری جهت بازگشایی (حداقل ۱۰ کاراکتر) |
| `custom_requires_qa_gate` | Check | پیش‌فرض: ۰ | محافظت‌شده؛ فقط `Quality Manager` |
| `custom_cycle_added_at` | Datetime | فقط‌خواندنی | زمان تخصیص به سایکل جهت سنجش خزش |
| `custom_mandatory_protocols` | Table | `Task Mandatory Protocol` | محافظت‌شده؛ فقط `Quality Manager` |

---

### ۱.۲. داک‌تایپ مستقل `Work Team`
* **`team_name`** (Data - یکتا، اجباری)
* **`team_lead`** (Link $\rightarrow$ `User` - اجباری)
* **`capacity_unit`** (Select: `Story Points\nMachine Hours` - اجباری)
* **`cycle_duration_weeks`** (Int - پیش‌فرض: ۲)
* **`flavor`** (Select: `Flavor A (Medical Consumables)\nFlavor B (Plasma Devices)\nFlavor C (Tooling/Molds)\nFlavor D (Cleanroom Ops)`)

---

### ۱.۳. داک‌تایپ مستقل `Work Cycle`
* **`cycle_name`** (Data - یکتا، اجباری)
* **`team`** (Link $\rightarrow$ `Work Team` - اجباری)
* **`status`** (Select: `Upcoming\nActive\nCompleted\nCancelled\nRollover Failed` - اجباری)
* **`start_date`** (Date - اجباری)
* **`end_date`** (Date - اجباری)
* **`baseline_points`** (Float - خط‌پایه استوری‌پوینت)
* **`baseline_machine_hours`** (Float - خط‌پایه ساعت ماشین طعم C)
* **`baseline_frozen`** (Check - فریز خط‌پایه در شنبه ساعت ۱۴)
* **`baseline_frozen_at`** (Datetime - زمان دقیق فریز)
* **`metrics_snapshotted`** (Check - حفظ اسنپ‌شات متریک‌ها در اولین اجرا)
* **`velocity_points`** (Float - سرعت تحقق‌یافته استوری‌پوینت)
* **`velocity_hours`** (Float - سرعت تحقق‌یافته ساعت ماشین)
* **`scope_creep_percent`** (Float - درصد خزش دامنه متناسب با واحد تیم)
* **`scope_creep_note`** (Small Text - لاگ تحلیلی خزش)
* **`failure_notes`** (Small Text - گزارش خطاهای رول‌اور در صورت بروز اشکال)
* **`rollover_execution_id`** (Data - شناسه یکتای UUID اجرای رول‌اور)

---

### ۱.۴. داک‌تایپ مستقل `Engineering Test Record` (`is_submittable = 1`)
* **`task`** (Link $\rightarrow$ `Task` - اجباری)
* **`protocol_code`** (Data - اجباری)
* **`protocol_name`** (Data - اجباری)
* **`result`** (Select: `Pass\nFail` - اجباری)
* **`tested_by`** (Link $\rightarrow$ `User` - اجباری؛ اپراتور مجری تست)
* **`qa_approver`** (Link $\rightarrow$ `User` - اجباری؛ دارنده نقش `Quality Manager`)
* **`test_date`** (Date - اجباری)
* **`sample_id`** (Data - شناسه بچ/نمونه)
* **`equipment_id`** (Data - شناسه تجهیز و وضعیت کالیبراسیون)
* **`submitted_at`** (Datetime - فقط‌خواندنی؛ زمان دقیق سابمیت امضای الکترونیک)
* **`approval_signature`** (Data - فقط‌خواندنی؛ هش امضای دیجیتال تاییدکننده)

---

### ۱.۵. داک‌تایپ مستقل `Task Cycle History` (Append-Only)
* **`task`** (Link $\rightarrow$ `Task` - اجباری)
* **`from_cycle`** (Link $\rightarrow$ `Work Cycle`)
* **`to_cycle`** (Link $\rightarrow$ `Work Cycle`)
* **`event_type`** (Select: `Rollover\nBlocked\nReview Required\nEviction\nUnscheduled\nManual Transfer` - اجباری)
* **`rollover_count`** (Int)
* **`is_blocked`** (Check)
* **`reason`** (Small Text)
* **`execution_id`** (Data)
* **`moved_at`** (Datetime)

---

### ۱.۶. داک‌تایپ فرزند `Task Mandatory Protocol` (Child Table)
* **`protocol_code`** (Data - اجباری)
* **`protocol_name`** (Data - اجباری)

---

## ۲. ماتریس ماشین وضعیت و نگاشت وضعیت‌ها (State Machine Synchronization)

* `Triage` $\longleftrightarrow$ `Task.status = "Open"`
* `Backlog` $\longleftrightarrow$ `Task.status = "Open"`
* `Unstarted` $\longleftrightarrow$ `Task.status = "Open"`
* `Started` $\longleftrightarrow$ `Task.status = "Working"`
* `Completed` $\longleftrightarrow$ `Task.status = "Completed"` (قفل‌شده با گیت QA)
* `Cancelled` $\longleftrightarrow$ `Task.status = "Cancelled"`

**قواعد گذار وضعیت:**
۱. گذار از `Cancelled` به `Completed` به صورت مستقیم اکیداً ممنوع است و خطا می‌دهد.
۲. گذار از `Completed` یا `Cancelled` به هر وضعیت فعال، مشروط به داشتن نقش `Quality Manager` یا `System Manager` و ثبت حداقل ۱۰ کاراکتر دلیل در `custom_reopen_reason` است.
۳. در صورت لغو رکورد آزمون Pass در یک تسک تکمیل‌شده، وضعیت تسک بلافاصله از `Completed` به `Started` بازمی‌گردد.

---

## ۳. محدوده نسخه ۱ (Scope Boundary Statement)

موارد زیر **رسماً خارج از دامنه فاز ۱ (Out of Scope for v1)** تعریف شده‌اند و در فازهای بعدی پس از تایید پایلوت توسعه خواهند یافت:
۱. موتور SLA خودکار برای صف تریاژ (Triage Auto-Escalation).
۲. گردش کار خودکار صدور برون‌سپاری با انبار امانی (Subcontracting Consignment Automated Workflow).
۳. درخت سلسله‌مراتبی وابستگی پیشرفته تسک‌ها (`blocked_by` گراف حلقوی).

# راهنمای جامع استقرار و راه‌اندازی پکیج Hamerz EOMS روی Frappe / ERPNext
## ویژه مهندس IT و مدیر سیستم (DevOps / System Administrator)

---

### ۱. مشخصات بسته نرم‌افزاری (Package Metadata)

* **نام اپلیکیشن:** `hamerz_eoms` (ماژول: `EOMS`)
* **نسخه بسته:** `v1.4.6` (Phase 1 & 2 Definitive Release)
* **سازمان:** **شرکت سولار الکتریک انرژی (سهامی خاص)** — شهرک صنعتی نجف‌آباد ۲
* **پلتفرم هدف:** Frappe Framework v15 / v16 & ERPNext v15 / v16
* **محیط پایتون موردنیاز:** Python >= 3.10
* **پایگاه داده هدف:** MariaDB >= 10.6 یا PostgreSQL >= 14
* **شناسه کامیت مرجع گیت:** `2d71f40`

---

### ۲. پیش‌نیازهای سرور پیش از نصب (Server Prerequisites)

پیش از اقدام به نصب، اطمینان حاصل کنید:
1. کاربری با دسترسی به بنچ (`frappe` user) در ترمینال فعال باشد.
2. سایت موردنظر در بنچ فعال و در حال سرویس‌دهی باشد (مثلاً `erp.solar.ir` یا `site1.local`).
3. پیش‌نیازهای ساخت پایتون (`flit_core`) در محیط مجازی بنچ نصب باشد:
   ```bash
   ./env/bin/pip install flit_core
   ```

---

### ۳. گام‌های گام‌به‌گام استقرار بر روی سرور (Deployment Steps)

کلیه فرامین زیر را در پوشه اصلی بنچ (جایی که فایل `Procfile` و پوشه‌های `apps` و `sites` قرار دارند) اجرا نمایید:

```bash
cd /home/frappe/frappe-bench
```

#### گام اول: انتقال سورس کد به پوشه `apps`
پوشه `hamerz_eoms` را در مسیر `apps/hamerz_eoms` قرار دهید (یا فایل زیپ را در این مسیر Extract کنید):
```bash
# ساختار مسیر باید دقیقاً به این شکل باشد:
# /home/frappe/frappe-bench/apps/hamerz_eoms/pyproject.toml
# /home/frappe/frappe-bench/apps/hamerz_eoms/hamerz_eoms/hooks.py
```

#### گام دوم: ثبت و نصب پکیج در محیط مجازی پایتون
```bash
./env/bin/pip install -e ./apps/hamerz_eoms
```

#### گام سوم: نصب اپلیکیشن روی سایت هدف
```bash
bench --site <site_name> install-app hamerz_eoms
```
*(به‌جای `<site_name>` نام سایت خود را قرار دهید؛ مثلاً `bench --site erp.solar.ir install-app hamerz_eoms`)*

#### گام چهارم: اعمال تغییرات پایگاه داده، داک‌تایپ‌ها و Fixtureها
این فرمان، فیلدهای سفارشی (Custom Fields روی Task و Project)، نقش `Quality Manager` و دسترسی‌ها را لود می‌کند:
```bash
bench --site <site_name> migrate
```

#### گام پنجم: پاک‌سازی کش و ری‌استارت سرویس‌ها
```bash
bench --site <site_name> clear-cache
bench restart
```

---

### ۴. راستی‌آزمایی و اجرای سوییت تست‌های خودکار (Automated Verification)

جهت حصول اطمینان ۱۰۰٪ از صحت استقرار و فعال‌بودن گیت‌های کیفی، سوییت آزمون ۲۴ سناریویی را اجرا کنید:

```bash
# ۱. فعال‌سازی موقت فلگ تست روی سایت
bench --site <site_name> set-config allow_tests true

# ۲. اجرای آزمون‌های یکپارچه‌سازی EOMS
bench --site <site_name> run-tests --app hamerz_eoms

# ۳. غیرفعال‌سازی فلگ تست پس از اتمام
bench --site <site_name> set-config allow_tests false
```

**نتیجه مورد انتظار:**
هر ۲۴ آزمون با وضعیت `OK` و به رنگ سبز عبور کنند (`Ran 24 tests in ... OK`).

---

### ۵. بررسی جاب‌های زمان‌بندی‌شده (Scheduler Crons Verification)

بررسی کنید که ۳ زمان‌بند سیستمی به صف سرور اضافه شده باشند:
```bash
bench --site <site_name> show-jobs
```
رویدادهای زیر در جدول خروجی ظاهر خواهند شد:
* `hamerz_eoms.operations.scheduler.close_due_cycles` (کرون: `55 23 * * *` — هر شب ساعت ۲۳:۵۵)
* `hamerz_eoms.operations.scheduler.activate_upcoming_cycles` (کرون: `0 6 * * 6` — شنبه‌ها ساعت ۰۶:۰۰)
* `hamerz_eoms.operations.scheduler.freeze_cycle_baselines` (کرون: `0 14 * * 6` — شنبه‌ها ساعت ۱۴:۰۰)

---

### ۶. ساختار ماژول و اجزای نصب‌شده (Installed Artifacts)

پس از نصب، اجزای زیر به محیط ERPNext اضافه می‌گردند:

1. **داک‌تایپ‌های اختصاصی (DocTypes):**
   * `Work Team` (مدیریت تیم‌های عملیاتی و طعم‌های مهندسی A, B, C, D)
   * `Work Cycle` (مدیریت چرخه‌ها، فریز بیس‌لاین و لاگ‌های رول‌اور)
   * `Engineering Test Record` (ثبت آزمون‌های کیفی ایزو ۱۳۴۸۵ و امضای دیجیتال)
   * `Task Mandatory Protocol` (جدول فرزند پروتکل‌های اجباری کیفی روی Task)
   * `Task Cycle History` (ردیابی حسابرسی جابجایی تسک‌ها بین چرخه‌ها)

2. **فیلدهای سفارشی (Custom Fields):**
   * ۱۵ فیلد سفارشی روی داک‌تایپ `Task` (تیم، چرخه، وضعیت کانبان، ساعت ماشین، استوری پوینت، تیک QA، پروتکل‌ها، دلایل مسدودی و بازگشایی).
   * ۲ فیلد سفارشی روی داک‌تایپ `Project` (اجبار گیت کیفیت سازمانی، طعم مهندسی پروژه).

3. **نقش‌های دسترسی (Roles):**
   * `Quality Manager` (مدیر تضمین کیفیت — تنها نقش مجاز برای ثبت Pass آزمون‌های مهندسی و بازگشایی تسک‌های Completed).

---

### ۷. تماس و پشتیبانی فنی
در صورت بروز هرگونه سوال در زمان استقرار، کدهای سورس همراه با کامنت‌های کامل، تایپ هینت‌ها و ساختار دیتابیس در خود پکیج مستند شده‌اند.

#!/usr/bin/env bash
# ==============================================================================
# Hamerz Plasma Shopfloor Management System - Production Runner
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-8090}"

echo "======================================================================"
echo "🚀 راه‌اندازی سامانه مدیریت کارگاهی و کنترل پروژه شرکت هامرز پلاسما"
echo "🌐 پورت اجرایی: $PORT"
echo "📁 دایرکتوری سامانه: $SCRIPT_DIR"
echo "======================================================================"

if ! command -v python3 &> /dev/null; then
    echo "❌ خطا: پایتون ۳ بر روی سیستم نصب نیست. لطفاً پایتون ۳.۹ یا بالاتر را نصب نمایید."
    exit 1
fi

echo "🔍 بررسی سلامت پایگاه‌داده..."
python3 -c "
import sqlite3, os
db = os.path.join('$SCRIPT_DIR', 'hamerz_shopfloor.db')
if not os.path.exists(db):
    print('❌ پایگاه‌داده یافت نشد!')
    exit(1)
conn = sqlite3.connect(db)
c = conn.cursor()
c.execute('SELECT count(*) FROM operations')
print(f'✅ پایگاه‌داده آماده است ({c.fetchone()[0]} ردیف عملیات موجود است).')
conn.close()
"

echo "🌟 در حال اجرای سرور وب و وب‌سرویس‌های REST..."
echo "🔗 آدرس دسترسی محلی: http://localhost:$PORT"
echo "برای توقف سرور کلیدهای Ctrl + C را بفشارید."

exec python3 "$SCRIPT_DIR/app.py"

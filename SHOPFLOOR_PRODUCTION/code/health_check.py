#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hamerz Plasma Shopfloor - Rapid Production Health Check Script
"""
import os, sys, sqlite3, json

def check_system():
    print("=================================================================")
    print("🔍 RAPID PRODUCTION HEALTH CHECK: HAMERZ SHOPFLOOR SYSTEM")
    print("=================================================================")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. Check critical files
    files = [
        "app.py",
        "index.html",
        "hamerz_shopfloor_app_4.html",
        "hamerz_shopfloor.db",
        "ai_machining_estimator.py",
        "catia_folder_scanner.py",
        "frappe_integration_service.py"
    ]
    missing = [f for f in files if not os.path.exists(os.path.join(base_dir, f))]
    if missing:
        print(f"❌ Missing critical files: {missing}")
        return False
    print("✅ All 7 core production source files exist.")

    # 2. Check Database integrity & schemas
    db_path = os.path.join(base_dir, "hamerz_shopfloor.db")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT count(*) FROM operations")
    op_count = c.fetchone()[0]
    
    c.execute("SELECT count(*) FROM users")
    user_count = c.fetchone()[0]

    c.execute("SELECT count(*) FROM tool_inventory")
    tool_count = c.fetchone()[0]
    
    # Check orphans
    c.execute("SELECT count(*) FROM daily_allocations a LEFT JOIN operations o ON a.operation_id = o.operation_id WHERE o.operation_id IS NULL")
    orphan_alloc = c.fetchone()[0]

    c.execute("SELECT count(*) FROM machine_time_entries m LEFT JOIN daily_execution_logs l ON m.log_id = l.log_id WHERE l.log_id IS NULL")
    orphan_time = c.fetchone()[0]
    conn.close()

    print(f"✅ SQLite Database valid:")
    print(f"   - Operations: {op_count}")
    print(f"   - Users & Roles: {user_count}")
    print(f"   - Tool Inventory: {tool_count}")
    print(f"   - Orphan Allocations: {orphan_alloc}")
    print(f"   - Orphan Time Entries: {orphan_time}")

    if orphan_alloc > 0 or orphan_time > 0:
        print("❌ Warning: Database contains orphan records.")
        return False

    print("=================================================================")
    print("🎉 ALL PRODUCTION CHECKS PASSED: SYSTEM READY FOR DEPLOYMENT!")
    print("=================================================================")
    return True

if __name__ == "__main__":
    success = check_system()
    sys.exit(0 if success else 1)

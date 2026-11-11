# Hamerz EOMS (Engineering Operating Management System)

> **Version:** 1.4.6  
> **Target Platform:** Frappe / ERPNext v16  
> **Publisher:** Solar Electric Energy Co. (سولار الکتریک انرژی - سهامی خاص) / Hamerz Plasma  
> **Status:** Candidate Baseline (Syntax-Checked, Packaged for Staging Verification)

---

## Overview

**Hamerz EOMS** provides high-rigor, Linear-style agile operations and ISO 13485-aligned engineering quality gates for Frappe/ERPNext v16. It is designed for multi-flavor industrial environments:
- **Flavor A:** Medical Consumables R&D
- **Flavor B:** Cold Atmospheric Plasma Devices
- **Flavor C:** Precision Tooling & Injection Molds (Machine Hours Capacity)
- **Flavor D:** Cleanroom Operations

## Key Features

1. **Work Cycle & Rollover Engine:**
   - Atomic, retry-safe cycle rollover with row-level database locking (`SELECT ... FOR UPDATE`).
   - Alphanumeric savepoints (`eoms_sp_{idx}`) for fault-tolerant task migrations.
   - Distinct handling of Scope Creep across Story Points (R&D) and Machine Hours (Tooling).
   - Concurrency deduplication and date-scoped queue IDs (`eoms_rollover_{cycle}_{date}`).
   - Clean detachment of mismatched team tasks and rollover of blocked tasks without penalty.

2. **Quality & Compliance Gates:**
   - Pre-completion validation against mandatory engineering protocols with zero-protocol bypass protection.
   - Segregation of duties between `tested_by` and `qa_approver` (Quality Manager).
   - Strict Done-Lock on Completed tasks with formal reopening justifications.
   - Superseded Pass protection: Latest test cancellation revokes QA pass even if older pass exists.
   - Immediate demotion of completed tasks upon revocation of qualifying test records unless newer pass exists.
   - Prohibition of new test submissions against already finalized (Completed/Cancelled) tasks without formal reopening.

3. **Bidirectional State Synchronization:**
   - Seamless mapping between native Frappe `Task.status` and Linear-style `custom_state_category` without data corruption.

## Installation & Testing on Staging Bench

```bash
# 1. Fetch app into bench
bench get-app /path/to/hamerz_eoms

# 2. Install on test site
bench --site <site_name> install-app hamerz_eoms

# 3. Run database migrations and load fixtures
bench --site <site_name> migrate

# 4. Enable test execution and run automated suite
bench --site <site_name> set-config allow_tests true
bench --site <site_name> run-tests --app hamerz_eoms
```

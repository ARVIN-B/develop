# -*- coding: utf-8 -*-
# file: hamerz_eoms/hamerz_eoms/operations/scheduler.py

import hashlib
import frappe
from frappe import _
from frappe.utils import now_datetime, getdate
from hamerz_eoms.operations.rollover import execute_cycle_rollover, _record_task_cycle_history

def close_due_cycles(sync_execution: bool = False):
    """
    Cron: '55 23 * * *' (Nightly at 23:55).
    1. FIRST cleans up expired Upcoming cycles (e.g. after server/bench downtime):
       - Atomically detaches each task with individual savepoint + audit history.
       - Strictly guarantees cycle is NOT cancelled if any task fails detachment or remains attached.
    2. SECOND finds and rolls over all Active AND Rollover Failed cycles whose end date has arrived.
    3. Uses ASCII-safe SHA-256 hash in RQ job_id to prevent Unicode queue exceptions on Persian names.
    """
    logger = frappe.logger("eoms_scheduler")
    logger.info("Executing close_due_cycles nightly scheduled job.")

    # 1. Cleanup expired Upcoming cycles FIRST (whose entire scheduled duration elapsed during downtime)
    expired_upcoming = frappe.get_all(
        "Work Cycle",
        filters={
            "status": "Upcoming",
            "end_date": ["<", getdate()]
        },
        fields=["name", "team", "end_date"]
    )

    for exp_c in expired_upcoming:
        try:
            attached_tasks = frappe.get_all(
                "Task",
                filters={"custom_cycle": exp_c.name, "is_group": 0},
                fields=["name", "custom_rollover_count", "custom_is_blocked"]
            )

            failed_tasks = []
            for idx, at in enumerate(attached_tasks):
                sp_name = f"eoms_sp_exp_{idx}"
                frappe.db.savepoint(sp_name)
                try:
                    at_doc = frappe.get_doc("Task", at.name)
                    at_doc.flags.ignore_permissions = True
                    at_doc.flags.in_cycle_rollover = True
                    at_doc.custom_cycle = None
                    at_doc.save()

                    _record_task_cycle_history(
                        task=at.name,
                        from_cycle=exp_c.name,
                        to_cycle=None,
                        rollover_count=at.custom_rollover_count or 0,
                        is_blocked=1 if at.custom_is_blocked else 0,
                        event_type="Unscheduled",
                        reason=f"Detached because cycle '{exp_c.name}' expired in Upcoming status and was cancelled.",
                        execution_id="expired_upcoming_cleanup"
                    )
                except Exception as t_err:
                    frappe.db.rollback(save_point=sp_name)
                    failed_tasks.append({"task": at.name, "error": str(t_err)})
                    logger.error(f"Error detaching task {at.name} from expired cycle {exp_c.name}: {str(t_err)}")
                    try:
                        frappe.log_error(
                            title=f"EOMS Task Expired Cleanup Error: {at.name}",
                            message=f"Cycle: {exp_c.name}\nError: {frappe.get_traceback()}",
                            defer_insert=True
                        )
                    except Exception:
                        pass

            # Strict Guard: Check remaining attached tasks directly in DB
            remaining_count = frappe.db.count("Task", filters={"custom_cycle": exp_c.name, "is_group": 0})

            exp_doc = frappe.get_doc("Work Cycle", exp_c.name)
            exp_doc.flags.ignore_permissions = True

            if failed_tasks or remaining_count > 0:
                err_msg = (
                    f"Cleanup Incomplete: {len(failed_tasks)} tasks failed detachment. "
                    f"{remaining_count} tasks still attached. Cycle remains Upcoming for retry."
                )
                logger.error(f"Cycle {exp_c.name} cannot be cancelled: {err_msg}")
                exp_doc.failure_notes = err_msg
                exp_doc.save()
                try:
                    frappe.log_error(
                        title=f"EOMS Expired Cycle Cleanup Blocked: {exp_c.name}",
                        message=err_msg,
                        defer_insert=True
                    )
                except Exception:
                    pass
            else:
                exp_doc.status = "Cancelled"
                exp_doc.failure_notes = _(
                    "Cycle expired in Upcoming state without activation (end_date {0} in the past). "
                    "All attached tasks cleanly detached."
                ).format(exp_c.end_date)
                exp_doc.save()
                logger.warning(f"Expired upcoming cycle {exp_c.name} transitioned to Cancelled.")

        except Exception as exp_err:
            logger.error(f"Error processing expired upcoming cycle {exp_c.name}: {str(exp_err)}")

    # 2. Active & Failed cycles due for rollover
    due_cycles = frappe.get_all(
        "Work Cycle",
        filters={
            "status": ["in", ["Active", "Rollover Failed"]],
            "end_date": ["<=", getdate()]
        },
        fields=["name", "team"]
    )

    today_str = str(getdate())
    for cycle in due_cycles:
        try:
            if sync_execution:
                execute_cycle_rollover(cycle.name)
            else:
                # ASCII-safe hash prevents RQ redis queue errors with Persian/spaced cycle names
                safe_hash = hashlib.sha256(cycle.name.encode("utf-8")).hexdigest()[:16]
                frappe.enqueue(
                    "hamerz_eoms.operations.rollover.execute_cycle_rollover",
                    cycle_name=cycle.name,
                    queue="long",
                    timeout=1500,
                    job_id=f"eoms_rollover_{safe_hash}_{today_str}",
                    deduplicate=True
                )
        except Exception as e:
            logger.error(f"Failed to trigger rollover for cycle {cycle.name}: {str(e)}")
            try:
                frappe.log_error(
                    title=f"Cycle Closure Enqueue Failed: {cycle.name}",
                    message=frappe.get_traceback(),
                    defer_insert=True
                )
            except Exception:
                pass


def activate_upcoming_cycles():
    """
    Cron: '0 6 * * 6' (Saturday 06:00).
    Activates Upcoming cycles whose start date has arrived.
    Guarantees:
    1. Validates end_date >= start_date.
    2. Serializes activation per team via row lock on Work Team to prevent race conditions across parallel workers.
    3. Re-verifies team existence, dates, and Active cycle status inside the locked transaction.
    4. Uses per-cycle savepoints to ensure caller/test transactions are never wiped out on early exit.
    """
    logger = frappe.logger("eoms_scheduler")
    logger.info("Executing activate_upcoming_cycles scheduled job.")

    upcoming_cycles = frappe.get_all(
        "Work Cycle",
        filters={
            "status": "Upcoming",
            "start_date": ["<=", getdate()],
            "end_date": [">=", getdate()]
        },
        fields=["name", "team", "start_date", "end_date"],
        order_by="start_date asc"
    )

    for idx, c in enumerate(upcoming_cycles):
        if getdate(c.end_date) < getdate(c.start_date):
            logger.error(f"Cycle {c.name} has invalid dates (end_date < start_date). Skipping.")
            continue

        sp_name = f"eoms_sp_act_{idx}"
        frappe.db.savepoint(sp_name)
        try:
            # 1. Row lock on Work Team; verify team exists
            team_lock = frappe.db.sql(
                "SELECT name FROM `tabWork Team` WHERE name = %s FOR UPDATE",
                (c.team,), as_dict=True
            )
            if not team_lock:
                logger.error(f"Team '{c.team}' for cycle {c.name} does not exist. Skipping.")
                frappe.db.rollback(save_point=sp_name)
                continue

            # 2. Re-read cycle status and dates under lock
            cycle_fresh = frappe.db.get_value(
                "Work Cycle", c.name,
                ["status", "start_date", "end_date"],
                as_dict=True
            )
            if not cycle_fresh or cycle_fresh.status != "Upcoming":
                frappe.db.rollback(save_point=sp_name)
                continue

            if getdate(cycle_fresh.start_date) > getdate() or getdate(cycle_fresh.end_date) < getdate():
                frappe.db.rollback(save_point=sp_name)
                continue

            # 3. Check inside locked transaction if team already has an active cycle
            active_exists = frappe.db.sql("""
                SELECT name FROM `tabWork Cycle`
                WHERE team = %s AND status = 'Active' AND name != %s
            """, (c.team, c.name), as_dict=True)

            if active_exists:
                logger.warning(
                    f"Cannot activate cycle {c.name}: Team {c.team} already has an active cycle ({active_exists[0].name})."
                )
                frappe.db.rollback(save_point=sp_name)
                continue

            doc = frappe.get_doc("Work Cycle", c.name)
            doc.flags.ignore_permissions = True
            doc.status = "Active"
            doc.save()
            logger.info(f"Activated cycle {c.name} for team {c.team}.")

        except Exception as act_err:
            logger.error(f"Error activating cycle {c.name}: {str(act_err)}")
            frappe.db.rollback(save_point=sp_name)


def freeze_cycle_baselines():
    """
    Cron: '0 14 * * 6' (Saturday 14:00).
    Freezes committed baseline scope for active cycles.
    Guarantees:
    1. Row lock on Work Cycle to serialize baseline calculation.
    2. Re-verifies baseline_frozen == 0 and status == 'Active' inside lock.
    3. Atomically locks tasks during aggregation (FOR UPDATE) to prevent in-flight scope modifications.
    4. Handles Zero-Task edge case: If task_count == 0, planning is incomplete; postpone freeze rather than locking 0.
    5. Sets flags.in_baseline_freeze = True for authorized DocType validation.
    6. Uses per-cycle savepoints to preserve caller transaction boundaries.
    """
    logger = frappe.logger("eoms_scheduler")
    logger.info("Executing freeze_cycle_baselines scheduled job.")

    active_unfrozen_cycles = frappe.get_all(
        "Work Cycle",
        filters={
            "status": "Active",
            "baseline_frozen": 0,
            "start_date": ["<=", getdate()]
        },
        fields=["name", "team"]
    )

    for idx, c in enumerate(active_unfrozen_cycles):
        sp_name = f"eoms_sp_frz_{idx}"
        frappe.db.savepoint(sp_name)
        try:
            cycle_lock = frappe.db.sql("""
                SELECT name, status, baseline_frozen
                FROM `tabWork Cycle`
                WHERE name = %s
                FOR UPDATE
            """, (c.name,), as_dict=True)

            if not cycle_lock or cycle_lock[0].status != "Active" or cycle_lock[0].baseline_frozen:
                frappe.db.rollback(save_point=sp_name)
                continue

            # Atomically lock tasks under cycle to prevent concurrent point modifications during aggregation
            totals = frappe.db.sql("""
                SELECT 
                    COALESCE(SUM(custom_story_points), 0) AS sp_total,
                    COALESCE(SUM(custom_machine_hours), 0) AS mh_total,
                    COUNT(*) AS task_count
                FROM `tabTask`
                WHERE custom_cycle = %s 
                  AND custom_team = %s
                  AND is_group = 0
                  AND custom_state_category IN ('Unstarted', 'Started', 'Completed')
                FOR UPDATE
            """, (c.name, c.team), as_dict=True)[0]

            # Zero-Scope Policy: If 0 tasks exist, planning is incomplete. Postpone freeze.
            if totals.task_count == 0:
                logger.warning(
                    f"Cycle {c.name} (Team: {c.team}) has 0 tasks at freeze cutoff. "
                    "Planning incomplete; baseline freeze postponed."
                )
                frappe.db.rollback(save_point=sp_name)
                continue

            doc = frappe.get_doc("Work Cycle", c.name)
            doc.flags.in_baseline_freeze = True
            doc.flags.ignore_permissions = True
            doc.baseline_points = totals.sp_total
            doc.baseline_machine_hours = totals.mh_total
            doc.baseline_frozen = 1
            doc.baseline_frozen_at = now_datetime()
            doc.save()
            logger.info(
                f"Frozen baseline for cycle {c.name}: {totals.sp_total} SP / {totals.mh_total} MH ({totals.task_count} tasks)."
            )

        except Exception as frz_err:
            logger.error(f"Error freezing baseline for cycle {c.name}: {str(frz_err)}")
            frappe.db.rollback(save_point=sp_name)

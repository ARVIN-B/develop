# -*- coding: utf-8 -*-
# file: hamerz_eoms/hamerz_eoms/operations/rollover.py

import uuid
import frappe
from frappe import _
from frappe.utils import now_datetime, getdate

def execute_cycle_rollover(cycle_name: str) -> dict:
    """
    Executes an atomic, concurrency-safe rollover for an active or failed Work Cycle.
    
    Guarantees:
    1. Pre-condition Guards: status in ['Active', 'Rollover Failed'] and end_date <= today.
    2. Concurrency Control: Cycle-level row locking via SELECT ... FOR UPDATE.
    3. Isolated Savepoints: Alphanumeric savepoints (eoms_sp_{idx}) per task with rollback on failure.
    4. Comprehensive Leftover Check: ANY uncompleted tasks (from this or another team) block cycle completion.
    5. Mismatched Team & Unscheduled Resolution: Tasks from other teams or in Triage/Backlog are detached cleanly.
    6. Blocked Task Progression: Blocked tasks roll forward to next cycle without incrementing rollover counter.
    7. Accurate Scope Creep: Only pre-freeze rollovers into unfrozen cycles are exempted from scope creep.
    8. Transactional Error Logging: frappe.log_error with defer_insert=True preserves DB locks.
    """
    run_id = str(uuid.uuid4())
    logger = frappe.logger("eoms_rollover")
    logger.info(f"[{run_id}] Initiating cycle rollover for: {cycle_name}")

    # 1. Acquire DB Row-Level Lock on Work Cycle
    cycle_data = frappe.db.sql("""
        SELECT name, team, status, start_date, end_date,
               baseline_points, baseline_machine_hours, baseline_frozen, baseline_frozen_at,
               metrics_snapshotted, velocity_points, velocity_hours,
               scope_creep_percent, scope_creep_note
        FROM `tabWork Cycle`
        WHERE name = %s
        FOR UPDATE
    """, (cycle_name,), as_dict=True)

    if not cycle_data:
        frappe.throw(_("Work Cycle {0} not found.").format(cycle_name))

    current_cycle = cycle_data[0]

    if current_cycle.status not in ["Active", "Rollover Failed"]:
        logger.warning(f"[{run_id}] Cycle {cycle_name} status is '{current_cycle.status}'. Rollover skipped.")
        return {"status": "skipped", "reason": f"Cycle status is {current_cycle.status}"}

    if getdate(current_cycle.end_date) > getdate():
        frappe.throw(
            _("Cannot execute rollover: Cycle end date ({0}) has not arrived.").format(current_cycle.end_date),
            title=_("Premature Rollover Blocked")
        )

    # 2. Retrieve team capacity policy
    team_data = frappe.db.get_value(
        "Work Team", current_cycle.team,
        ["capacity_unit", "team_lead"],
        as_dict=True
    ) or {"capacity_unit": "Story Points", "team_lead": None}

    capacity_unit = team_data.get("capacity_unit") or "Story Points"

    # 3. Snapshot Metrics or reuse existing snapshot on retry
    cycle_doc = frappe.get_doc("Work Cycle", cycle_name)
    cycle_doc.flags.ignore_permissions = True

    if not current_cycle.metrics_snapshotted:
        metrics = _calculate_cycle_metrics_snapshot(current_cycle, capacity_unit)
        cycle_doc.velocity_points = metrics["velocity_points"]
        cycle_doc.velocity_hours = metrics["velocity_hours"]
        cycle_doc.scope_creep_percent = metrics["scope_creep_percent"]
        cycle_doc.scope_creep_note = metrics["scope_creep_note"]
        cycle_doc.metrics_snapshotted = 1
        cycle_doc.save()
    else:
        metrics = {
            "velocity_points": current_cycle.velocity_points,
            "velocity_hours": current_cycle.velocity_hours,
            "scope_creep_percent": current_cycle.scope_creep_percent,
            "scope_creep_note": current_cycle.scope_creep_note
        }

    # 4. Locate next valid (non-expired) cycle
    next_cycle = frappe.db.get_value(
        "Work Cycle",
        filters={
            "team": current_cycle.team,
            "status": ["in", ["Upcoming", "Active"]],
            "start_date": [">=", current_cycle.end_date],
            "end_date": [">=", getdate()],
            "name": ["!=", cycle_name]
        },
        fieldname=["name", "start_date", "status", "baseline_frozen"],
        as_dict=True,
        order_by="start_date asc"
    )
    next_cycle_name = next_cycle.name if next_cycle else None

    # 5. Cleanly detach mismatched team tasks OR Backlog/Triage tasks attached to this cycle
    mismatched_tasks = frappe.db.sql("""
        SELECT name, custom_team, custom_state_category, custom_rollover_count, custom_is_blocked
        FROM `tabTask`
        WHERE custom_cycle = %s
          AND is_group = 0
          AND (
              (
                  (custom_team IS NULL OR custom_team != %s)
                  AND custom_state_category IN ('Triage', 'Backlog', 'Unstarted', 'Started')
              )
              OR custom_state_category IN ('Triage', 'Backlog')
          )
    """, (cycle_name, current_cycle.team), as_dict=True)

    summary = {
        "cycle": cycle_name,
        "team": current_cycle.team,
        "run_id": run_id,
        "processed_tasks": 0,
        "rolled_over_tasks": [],
        "evicted_tasks": [],
        "unscheduled_tasks": [],
        "failed_tasks": [],
        "mismatched_team_tasks": [
            t.name for t in mismatched_tasks if (t.custom_team is None or t.custom_team != current_cycle.team)
        ]
    }

    for idx, mt in enumerate(mismatched_tasks):
        sp_name = f"eoms_sp_mismatch_{idx}"
        frappe.db.savepoint(sp_name)
        try:
            mt_doc = frappe.get_doc("Task", mt.name)
            mt_doc.flags.ignore_permissions = True
            mt_doc.flags.in_cycle_rollover = True
            mt_doc.custom_cycle = None
            mt_doc.save()

            if mt.custom_team != current_cycle.team:
                detach_reason = f"Detached during rollover: Task team '{mt.custom_team}' mismatches cycle team '{current_cycle.team}'."
            else:
                detach_reason = f"Detached during rollover: Task remained in '{mt.custom_state_category}' state at cycle closure."

            _record_task_cycle_history(
                task=mt.name, from_cycle=cycle_name, to_cycle=None,
                rollover_count=mt.custom_rollover_count or 0,
                is_blocked=1 if mt.custom_is_blocked else 0,
                event_type="Unscheduled",
                reason=detach_reason,
                execution_id=run_id
            )
            logger.info(f"[{run_id}] Detached task {mt.name} from cycle {cycle_name}.")
        except Exception as mt_err:
            frappe.db.rollback(save_point=sp_name)
            logger.error(f"[{run_id}] Error detaching task {mt.name}: {str(mt_err)}")
            # Crucial: Failure to detach a task is recorded as a failure so cycle cannot complete!
            summary["failed_tasks"].append({"task": mt.name, "error": str(mt_err)})
            try:
                frappe.log_error(
                    title=f"EOMS Mismatched Task Detach Error: {mt.name}",
                    message=f"Cycle: {cycle_name}\nError: {frappe.get_traceback()}",
                    defer_insert=True
                )
            except Exception:
                pass

    # 6. Query uncompleted tasks strictly assigned to this cycle & team
    uncompleted_tasks = frappe.get_all(
        "Task",
        filters={
            "custom_cycle": cycle_name,
            "custom_team": current_cycle.team,
            "custom_state_category": ["in", ["Unstarted", "Started"]],
            "is_group": 0
        },
        fields=[
            "name", "subject", "owner", "_assign", "custom_task_lead",
            "custom_state_category", "custom_rollover_count",
            "custom_is_blocked"
        ]
    )

    # 7. Task Transition Loop with Individual Alphanumeric Savepoints
    for idx, t in enumerate(uncompleted_tasks):
        sp_name = f"eoms_sp_{idx}"
        frappe.db.savepoint(sp_name)

        try:
            task_doc = frappe.get_doc("Task", t.name)
            task_doc.flags.ignore_permissions = True
            task_doc.flags.in_cycle_rollover = True

            current_count = task_doc.custom_rollover_count or 0
            is_blocked = 1 if task_doc.custom_is_blocked else 0

            # --- BRANCH 1: Blocked Task (Carries forward without incrementing rollover count) ---
            if is_blocked:
                if next_cycle_name:
                    task_doc.custom_cycle = next_cycle_name
                    if next_cycle and not next_cycle.get("baseline_frozen"):
                        task_doc.custom_entered_via_rollover = 1
                        task_doc.custom_cycle_added_at = None
                    else:
                        task_doc.custom_entered_via_rollover = 0
                        task_doc.custom_cycle_added_at = now_datetime()

                    task_doc.save()

                    _record_task_cycle_history(
                        task=t.name, from_cycle=cycle_name, to_cycle=next_cycle_name,
                        rollover_count=current_count, is_blocked=1,
                        event_type="Blocked",
                        reason="Task is blocked. Transferred to next cycle without rollover count increment.",
                        execution_id=run_id
                    )
                    summary["rolled_over_tasks"].append(t.name)
                else:
                    task_doc.custom_cycle = None
                    task_doc.custom_state_category = "Backlog"
                    task_doc.status = "Open"
                    task_doc.custom_needs_review = 1
                    task_doc.save()

                    _record_task_cycle_history(
                        task=t.name, from_cycle=cycle_name, to_cycle=None,
                        rollover_count=current_count, is_blocked=1,
                        event_type="Blocked",
                        reason="Task is blocked with no next cycle scheduled. Detached to Backlog for resolution.",
                        execution_id=run_id
                    )
                    summary["evicted_tasks"].append(t.name)

            # --- BRANCH 2: Exceeded Rollover Threshold (>= 3) ---
            elif current_count >= 2:
                new_count = current_count + 1
                task_doc.custom_rollover_count = new_count
                task_doc.custom_needs_review = 1

                if task_doc.custom_state_category == "Started":
                    if next_cycle_name:
                        task_doc.custom_cycle = next_cycle_name
                        if next_cycle and not next_cycle.get("baseline_frozen"):
                            task_doc.custom_entered_via_rollover = 1
                            task_doc.custom_cycle_added_at = None
                        else:
                            task_doc.custom_entered_via_rollover = 0
                            task_doc.custom_cycle_added_at = now_datetime()

                        task_doc.save()

                        _record_task_cycle_history(
                            task=t.name, from_cycle=cycle_name, to_cycle=next_cycle_name,
                            rollover_count=new_count, is_blocked=0,
                            event_type="Review Required",
                            reason="Started task exceeded 3 rollovers. Rolled over with Review Required flag.",
                            execution_id=run_id
                        )
                        summary["rolled_over_tasks"].append(t.name)
                    else:
                        task_doc.custom_cycle = None
                        task_doc.save()
                        _record_task_cycle_history(
                            task=t.name, from_cycle=cycle_name, to_cycle=None,
                            rollover_count=new_count, is_blocked=0,
                            event_type="Review Required",
                            reason="Started task exceeded 3 rollovers with no next cycle. Unscheduled.",
                            execution_id=run_id
                        )
                        summary["unscheduled_tasks"].append(t.name)
                else:
                    task_doc.custom_cycle = None
                    task_doc.custom_state_category = "Backlog"
                    task_doc.status = "Open"
                    task_doc.save()

                    _record_task_cycle_history(
                        task=t.name, from_cycle=cycle_name, to_cycle=None,
                        rollover_count=new_count, is_blocked=0,
                        event_type="Eviction",
                        reason=f"Unstarted task exceeded rollover threshold ({new_count}). Evicted to Backlog.",
                        execution_id=run_id
                    )
                    summary["evicted_tasks"].append(t.name)

                _create_transactional_notification(
                    task_doc,
                    reason=f"Task reached rollover limit ({new_count}). PM review required.",
                    team_lead=team_data.get("team_lead")
                )

            # --- BRANCH 3: Standard Rollover (< 3) ---
            else:
                new_count = current_count + 1
                task_doc.custom_rollover_count = new_count

                if next_cycle_name:
                    task_doc.custom_cycle = next_cycle_name
                    if next_cycle and not next_cycle.get("baseline_frozen"):
                        task_doc.custom_entered_via_rollover = 1
                        task_doc.custom_cycle_added_at = None
                    else:
                        task_doc.custom_entered_via_rollover = 0
                        task_doc.custom_cycle_added_at = now_datetime()

                    task_doc.save()

                    _record_task_cycle_history(
                        task=t.name, from_cycle=cycle_name, to_cycle=next_cycle_name,
                        rollover_count=new_count, is_blocked=0,
                        event_type="Rollover",
                        reason="Standard cycle boundary transition to next cycle.",
                        execution_id=run_id
                    )
                    summary["rolled_over_tasks"].append(t.name)
                else:
                    if task_doc.custom_state_category != "Started":
                        task_doc.custom_state_category = "Backlog"
                        task_doc.status = "Open"
                    task_doc.custom_cycle = None
                    task_doc.save()

                    _record_task_cycle_history(
                        task=t.name, from_cycle=cycle_name, to_cycle=None,
                        rollover_count=new_count, is_blocked=0,
                        event_type="Unscheduled",
                        reason="No upcoming cycle found. Task unscheduled.",
                        execution_id=run_id
                    )
                    summary["unscheduled_tasks"].append(t.name)

            summary["processed_tasks"] += 1

        except Exception as task_err:
            frappe.db.rollback(save_point=sp_name)
            logger.error(f"[{run_id}] Error rolling over task {t.name}: {str(task_err)}")
            summary["failed_tasks"].append({"task": t.name, "error": str(task_err)})
            try:
                frappe.log_error(
                    title=f"EOMS Task Rollover Error: {t.name}",
                    message=f"Cycle: {cycle_name}\nError: {frappe.get_traceback()}",
                    defer_insert=True
                )
            except Exception:
                pass

    # 8. Comprehensive Leftover Zero-Check
    # Strictly checks if ANY non-terminal task (any team) remains attached to this cycle
    leftover_count = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabTask`
        WHERE custom_cycle = %s
          AND is_group = 0
          AND custom_state_category IN ('Triage', 'Backlog', 'Unstarted', 'Started')
    """, (cycle_name,))[0][0]

    cycle_doc.reload()
    cycle_doc.flags.ignore_permissions = True
    cycle_doc.flags.in_cycle_rollover = True
    cycle_doc.rollover_execution_id = run_id

    if summary["failed_tasks"] or leftover_count > 0:
        cycle_doc.status = "Rollover Failed"
        error_msg = f"Failed {len(summary['failed_tasks'])} tasks. Leftover uncompleted: {leftover_count}."
        cycle_doc.failure_notes = error_msg
        logger.error(f"[{run_id}] Cycle {cycle_name} marked Rollover Failed: {error_msg}")
        _alert_lead_on_partial_failure(current_cycle, summary["failed_tasks"], team_data.get("team_lead"))
    else:
        cycle_doc.status = "Completed"
        cycle_doc.failure_notes = None
        logger.info(f"[{run_id}] Cycle {cycle_name} completed successfully.")

    cycle_doc.save()
    return summary


def _record_task_cycle_history(task: str, from_cycle: str, to_cycle: str,
                               rollover_count: int, is_blocked: int,
                               event_type: str, reason: str, execution_id: str):
    """
    Dedicated history logger. Isolated to enable precise mocking in unit tests.
    """
    history_doc = frappe.get_doc({
        "doctype": "Task Cycle History",
        "task": task,
        "from_cycle": from_cycle,
        "to_cycle": to_cycle,
        "rollover_count": rollover_count,
        "is_blocked": is_blocked,
        "event_type": event_type,
        "reason": reason,
        "execution_id": execution_id,
        "moved_at": now_datetime()
    })
    history_doc.flags.ignore_permissions = True
    history_doc.insert()


def _calculate_cycle_metrics_snapshot(cycle_data, capacity_unit: str) -> dict:
    """
    Computes completed velocity and added scope based on the team's capacity unit.
    Strictly filters both Velocity and Scope Creep by custom_team to prevent cross-team data leaks.
    Excludes tasks added via pre-freeze automated rollover (entered_via_rollover = 1).
    """
    cycle_name = cycle_data["name"]
    team = cycle_data["team"]
    start_date = cycle_data["start_date"]
    baseline_threshold = cycle_data.get("baseline_frozen_at") or start_date

    # 1. Velocity (Strictly team filtered)
    velocity_data = frappe.db.sql("""
        SELECT 
            COALESCE(SUM(custom_story_points), 0) AS completed_sp,
            COALESCE(SUM(custom_machine_hours), 0) AS completed_mh
        FROM `tabTask`
        WHERE custom_cycle = %s 
          AND custom_team = %s 
          AND custom_state_category = 'Completed' 
          AND is_group = 0
    """, (cycle_name, team), as_dict=True)[0]

    # 2. Scope Creep (Strictly team filtered)
    if capacity_unit == "Machine Hours":
        baseline = cycle_data.get("baseline_machine_hours") or 0.0
        unit_label = "Machine Hours"
        added_scope = frappe.db.sql("""
            SELECT COALESCE(SUM(custom_machine_hours), 0) AS added
            FROM `tabTask`
            WHERE custom_cycle = %s 
              AND custom_team = %s 
              AND custom_cycle_added_at > %s 
              AND COALESCE(custom_entered_via_rollover, 0) = 0
              AND is_group = 0
        """, (cycle_name, team, baseline_threshold), as_dict=True)[0].added
    else:
        baseline = cycle_data.get("baseline_points") or 0.0
        unit_label = "Story Points"
        added_scope = frappe.db.sql("""
            SELECT COALESCE(SUM(custom_story_points), 0) AS added
            FROM `tabTask`
            WHERE custom_cycle = %s 
              AND custom_team = %s 
              AND custom_cycle_added_at > %s 
              AND COALESCE(custom_entered_via_rollover, 0) = 0
              AND is_group = 0
        """, (cycle_name, team, baseline_threshold), as_dict=True)[0].added

    if baseline > 0:
        creep_percent = round((added_scope / baseline) * 100, 2)
        note = f"Baseline: {baseline} {unit_label} | Added Scope: {added_scope} {unit_label}"
    else:
        creep_percent = None
        note = f"Baseline was zero (N/A). Absolute added scope: {added_scope} {unit_label}"

    return {
        "velocity_points": velocity_data.completed_sp,
        "velocity_hours": velocity_data.completed_mh,
        "scope_creep_percent": creep_percent,
        "scope_creep_note": note
    }


def _create_transactional_notification(task_doc, reason: str, team_lead: str):
    recipients = []
    if task_doc.custom_task_lead:
        recipients.append(task_doc.custom_task_lead)
    elif task_doc._assign:
        try:
            assignees = frappe.parse_json(task_doc._assign)
            if isinstance(assignees, list):
                recipients.extend(assignees)
        except Exception:
            pass

    if team_lead and team_lead not in recipients:
        recipients.append(team_lead)

    if not recipients and task_doc.owner:
        recipients.append(task_doc.owner)

    for user in recipients:
        if frappe.db.exists("User", user):
            frappe.get_doc({
                "doctype": "Notification Log",
                "subject": _("Task Cycle Eviction: {0}").format(task_doc.name),
                "email_content": _("Task '{0}' was detached from its cycle.<br><b>Reason:</b> {1}").format(
                    task_doc.subject, reason
                ),
                "for_user": user,
                "document_type": "Task",
                "document_name": task_doc.name
            }).insert(ignore_permissions=True)


def _alert_lead_on_partial_failure(cycle_data, failed_tasks: list, team_lead: str):
    if not team_lead or not frappe.db.exists("User", team_lead):
        return

    existing_todo = frappe.db.exists("ToDo", {
        "reference_type": "Work Cycle",
        "reference_name": cycle_data["name"],
        "status": "Open"
    })
    if existing_todo:
        return

    frappe.get_doc({
        "doctype": "ToDo",
        "description": _("Rollover failed for {0} tasks in Cycle {1}. Immediate review required.").format(
            len(failed_tasks), cycle_data["name"]
        ),
        "reference_type": "Work Cycle",
        "reference_name": cycle_data["name"],
        "allocated_to": team_lead,
        "priority": "High"
    }).insert(ignore_permissions=True)

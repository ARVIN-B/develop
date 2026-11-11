# -*- coding: utf-8 -*-
# file: hamerz_eoms/hamerz_eoms/quality/qa_gate.py

import datetime
import hashlib
import frappe
from frappe import _
from frappe.utils import now_datetime

def _normalize_val(val):
    """Normalizes field values to prevent false positives in Done-Lock comparisons."""
    if val is None:
        return ""
    if isinstance(val, (datetime.date, datetime.datetime)):
        return str(val)
    if isinstance(val, str):
        return val.strip()
    return val


def on_task_before_validate(doc, method=None):
    """
    1. Bidirectional State Synchronization between custom_state_category and Task.status.
    2. QA Gate requirement inheritance from Project.
    3. Scope Creep tracking (custom_cycle_added_at) and custom_entered_via_rollover lifecycle.
    4. Rollover counter reset on valid PM review clearance.
    """
    category_to_status = {
        "Triage": "Open",
        "Backlog": "Open",
        "Unstarted": "Open",
        "Started": "Working",
        "Completed": "Completed",
        "Cancelled": "Cancelled"
    }

    status_to_category = {
        "Open": "Unstarted",
        "Working": "Started",
        "Pending Review": "Started",
        "Completed": "Completed",
        "Cancelled": "Cancelled",
        "Template": "Backlog"
    }

    old_doc = doc.get_doc_before_save() if not doc.is_new() else None

    # 1. Bidirectional State Synchronization
    if doc.is_new():
        if doc.custom_state_category:
            doc.status = category_to_status.get(doc.custom_state_category, doc.status or "Open")
        elif doc.status:
            doc.custom_state_category = status_to_category.get(doc.status, "Backlog")
        else:
            doc.status = "Open"
            doc.custom_state_category = "Backlog"
    else:
        status_changed = (old_doc and doc.status != old_doc.status)
        category_changed = (old_doc and doc.custom_state_category != old_doc.custom_state_category)

        if category_changed and not status_changed:
            if doc.custom_state_category in category_to_status:
                doc.status = category_to_status[doc.custom_state_category]
        elif status_changed and not category_changed:
            if doc.status == "Completed":
                doc.custom_state_category = "Completed"
            elif doc.status == "Cancelled":
                doc.custom_state_category = "Cancelled"
            elif doc.status == "Working":
                doc.custom_state_category = "Started"
            elif doc.status == "Open" and doc.custom_state_category in ["Completed", "Cancelled", "Started"]:
                doc.custom_state_category = "Unstarted"
        elif not doc.custom_state_category and doc.status:
            doc.custom_state_category = status_to_category.get(doc.status, "Backlog")

    # 2. Inherit QA Gate requirement from Project
    if doc.project and not doc.custom_requires_qa_gate:
        proj_qa = frappe.db.get_value("Project", doc.project, "custom_requires_qa_gate")
        if proj_qa:
            doc.custom_requires_qa_gate = 1

    # 3. Scope Creep and Rollover Stamping
    in_rollover = getattr(doc.flags, "in_cycle_rollover", False) or getattr(doc.flags, "in_rollover", False)
    in_demotion = getattr(doc.flags, "in_qa_demotion", False)

    if not in_rollover and not in_demotion:
        if not doc.is_new():
            old_cycle = old_doc.custom_cycle if old_doc else frappe.db.get_value("Task", doc.name, "custom_cycle")
            if doc.custom_cycle and doc.custom_cycle != old_cycle:
                doc.custom_entered_via_rollover = 0
                doc.custom_cycle_added_at = now_datetime()

            old_review = old_doc.custom_needs_review if old_doc else frappe.db.get_value("Task", doc.name, "custom_needs_review")
            if old_review == 1 and not doc.custom_needs_review and doc.custom_cycle and doc.custom_cycle != old_cycle:
                doc.custom_rollover_count = 0
        elif doc.custom_cycle:
            if not doc.custom_cycle_added_at:
                doc.custom_cycle_added_at = now_datetime()


def validate_task_security_and_done_lock(doc, method=None):
    """
    Enforces Security and State Integrity:
    1. Reopen Guard: Only Quality Manager / Admin can reopen Completed or Cancelled tasks with valid justification.
    2. Direct Cancelled -> Completed and Completed -> Cancelled transitions are strictly forbidden.
    3. Done Lock: Completed tasks cannot modify operational fields or mandatory protocols without reopening.
    4. Child Table Security: Non-QA users cannot add, alter, or remove mandatory QA protocols.
    5. QA Gate Security: Non-QA users cannot disable custom_requires_qa_gate.
    """
    in_rollover = getattr(doc.flags, "in_cycle_rollover", False) or getattr(doc.flags, "in_rollover", False)
    in_demotion = getattr(doc.flags, "in_qa_demotion", False)
    if in_rollover or in_demotion:
        return

    user_roles = frappe.get_roles(frappe.session.user)
    is_qa_or_admin = "Quality Manager" in user_roles or "System Manager" in user_roles

    if not doc.is_new():
        old_doc = doc.get_doc_before_save()
        old_cat = old_doc.custom_state_category if old_doc else frappe.db.get_value("Task", doc.name, "custom_state_category")

        # 1. Reopen Guard
        if old_cat in ["Completed", "Cancelled"] and doc.custom_state_category not in ["Completed", "Cancelled"]:
            if not is_qa_or_admin:
                frappe.throw(
                    _("Security Violation: Only Quality Manager or System Manager can reopen a {0} task.").format(old_cat),
                    frappe.PermissionError
                )

            if not doc.custom_reopen_reason or len(doc.custom_reopen_reason.strip()) < 10:
                frappe.throw(
                    _("Reopening a {0} task requires an explanatory justification in 'custom_reopen_reason' (minimum 10 characters).").format(old_cat),
                    title=_("Reopen Guard Violation")
                )

            doc.add_comment(
                "Workflow",
                _("Task reopened from {0} to {1} by {2}. Justification: {3}").format(
                    old_cat, doc.custom_state_category, frappe.session.user, doc.custom_reopen_reason
                )
            )
            doc.custom_reopen_reason = None

        # 2. Forbidden State Transitions
        if old_cat == "Cancelled" and doc.custom_state_category == "Completed":
            frappe.throw(
                _("Invalid State Transition: A Cancelled task cannot be set directly to Completed. It must be reopened first."),
                title=_("Invalid State Machine Transition")
            )

        if old_cat == "Completed" and doc.custom_state_category == "Cancelled":
            frappe.throw(
                _("Invalid State Transition: A Completed task cannot be directly Cancelled. It must be formally reopened first."),
                title=_("Invalid State Machine Transition")
            )

        # 3. Done Lock on Completed Tasks
        if old_cat == "Completed" and doc.custom_state_category == "Completed":
            locked_fields = [
                "subject", "description", "expected_start_date", "expected_end_date",
                "custom_story_points", "custom_machine_hours", "custom_team", "custom_cycle",
                "project", "parent_task", "custom_requires_qa_gate"
            ]
            for fld in locked_fields:
                old_val = _normalize_val(old_doc.get(fld))
                new_val = _normalize_val(doc.get(fld))
                if old_val != new_val:
                    frappe.throw(
                        _("Done-Lock Violation: Field '{0}' cannot be modified on a Completed task without formal QA reopening.").format(fld),
                        title=_("Task Immutability Violation")
                    )

            # Freeze child table on Completed task
            old_protocols = [(p.protocol_code, p.protocol_name) for p in (old_doc.get("custom_mandatory_protocols") or [])]
            new_protocols = [(p.protocol_code, p.protocol_name) for p in (doc.get("custom_mandatory_protocols") or [])]
            if old_protocols != new_protocols:
                frappe.throw(
                    _("Done-Lock Violation: Mandatory QA protocols cannot be modified on a Completed task without formal QA reopening."),
                    title=_("Task Immutability Violation")
                )

        # 4. Mandatory Protocols Child Table Protection (even prior to completion)
        old_protocols = [(p.protocol_code, p.protocol_name) for p in (old_doc.get("custom_mandatory_protocols") or [])]
        new_protocols = [(p.protocol_code, p.protocol_name) for p in (doc.get("custom_mandatory_protocols") or [])]
        if old_protocols != new_protocols and not is_qa_or_admin:
            frappe.throw(
                _("Security Violation: Only Quality Manager or System Manager can modify mandatory QA protocols."),
                frappe.PermissionError
            )

        # 5. QA Gate Security: Only Quality Manager / Admin can disable custom_requires_qa_gate
        old_qa_req = old_doc.custom_requires_qa_gate if old_doc else 0
        if old_qa_req == 1 and not doc.custom_requires_qa_gate and not is_qa_or_admin:
            frappe.throw(
                _("Security Violation: Only Quality Manager or System Manager can disable the QA Gate requirement."),
                frappe.PermissionError
            )


def validate_project_qa_gate_toggle(doc, method=None):
    """Prevents unauthorized disabling of custom_requires_qa_gate on Project."""
    if doc.is_new():
        return
    old_doc = doc.get_doc_before_save()
    if old_doc and old_doc.custom_requires_qa_gate == 1 and not doc.custom_requires_qa_gate:
        user_roles = frappe.get_roles(frappe.session.user)
        if "Quality Manager" not in user_roles and "System Manager" not in user_roles:
            frappe.throw(
                _("Security Violation: Only Quality Manager or System Manager can disable Project QA Gate requirement."),
                frappe.PermissionError
            )


def validate_task_qa_gate(doc, method=None):
    """
    Hook on tabTask 'validate'.
    Enforces QA Gate validation ONLY upon transitioning into 'Completed'.
    Guarantees:
    1. If QA Gate is required, empty mandatory protocols list is an immediate error.
    2. Queries latest test record across docstatus IN (1, 2) ordered by creation DESC.
    3. If the latest record is cancelled (docstatus = 2), older records are invalid and gate fails.
    4. Latest record must be Pass with valid segregation of duties and authorized QA approver.
    """
    in_rollover = getattr(doc.flags, "in_cycle_rollover", False) or getattr(doc.flags, "in_rollover", False)
    in_demotion = getattr(doc.flags, "in_qa_demotion", False)
    if in_rollover or in_demotion:
        return

    entering_completed = (doc.custom_state_category == "Completed" or doc.status == "Completed")
    if not entering_completed:
        return

    if not doc.is_new():
        old_doc = doc.get_doc_before_save()
        old_was_completed = old_doc and (old_doc.custom_state_category == "Completed" or old_doc.status == "Completed")
        if old_was_completed:
            return

    requires_qa = doc.custom_requires_qa_gate
    if not requires_qa and doc.project:
        requires_qa = frappe.db.get_value("Project", doc.project, "custom_requires_qa_gate")

    if not requires_qa:
        return

    mandatory_protocols = doc.get("custom_mandatory_protocols") or []
    if not mandatory_protocols:
        frappe.throw(
            _("QA Gate Failure: Task requires QA Gate verification, but no mandatory protocols are assigned."),
            title=_("QA Gate Blocked")
        )

    for proto in mandatory_protocols:
        p_code = proto.protocol_code

        # Query latest test record across docstatus IN (1, 2) to respect cancellations
        latest_records = frappe.db.sql("""
            SELECT name, result, docstatus, tested_by, qa_approver, creation
            FROM `tabEngineering Test Record`
            WHERE task = %s AND protocol_code = %s AND docstatus IN (1, 2)
            ORDER BY creation DESC, name DESC
            LIMIT 1
        """, (doc.name, p_code), as_dict=True)

        if not latest_records:
            frappe.throw(
                _("QA Gate Failure: Mandatory protocol '{0}' has no submitted test record.").format(p_code),
                title=_("QA Gate Blocked")
            )

        latest_rec = latest_records[0]

        # If latest record was cancelled, prior Pass records are invalidated!
        if latest_rec.docstatus == 2:
            frappe.throw(
                _("QA Gate Failure: The latest test record '{0}' for protocol '{1}' was cancelled. Re-testing is required.").format(
                    latest_rec.name, p_code
                ),
                title=_("QA Gate Blocked")
            )

        if latest_rec.result != "Pass":
            frappe.throw(
                _("QA Gate Failure: Mandatory protocol '{0}' latest record '{1}' result is '{2}' (Pass required).").format(
                    p_code, latest_rec.name, latest_rec.result
                ),
                title=_("QA Gate Blocked")
            )

        if not latest_rec.tested_by or not latest_rec.qa_approver:
            frappe.throw(
                _("Compliance Incomplete: Test Record {0} requires both tested_by and qa_approver.").format(latest_rec.name),
                title=_("Compliance Incomplete")
            )

        if latest_rec.tested_by == latest_rec.qa_approver:
            frappe.throw(
                _("Segregation of Duties Violation: Tester ({0}) cannot approve their own test.").format(latest_rec.tested_by),
                title=_("Segregation of Duties")
            )

        approver_roles = frappe.get_roles(latest_rec.qa_approver)
        if "Quality Manager" not in approver_roles and "System Manager" not in approver_roles:
            frappe.throw(
                _("Compliance Violation: Approver '{0}' does not hold the 'Quality Manager' role.").format(latest_rec.qa_approver),
                title=_("Unauthorized QA Approver")
            )


def validate_test_record_submission(doc, method=None):
    """
    Hook on tabEngineering Test Record 'before_submit'.
    Guarantees:
    1. Blocks submission of new test records on already Completed or Cancelled tasks without reopening.
    2. Enforces segregation of duties between tester and QA approver.
    3. Calculates authentic SHA-256 Approval Integrity Digest.
    """
    if doc.task:
        task_info = frappe.db.get_value("Task", doc.task, ["custom_state_category", "status"], as_dict=True)
        if task_info:
            task_cat = task_info.custom_state_category or task_info.status
            if task_cat in ["Completed", "Cancelled"]:
                frappe.throw(
                    _("Compliance Violation: Cannot submit new test record for {0} task '{1}'. The task must be formally reopened first.").format(
                        task_cat, doc.task
                    ),
                    title=_("Test Submission Blocked on Finalized Task")
                )

    if not doc.tested_by or not doc.qa_approver:
        frappe.throw(
            _("Cannot submit test record: Both 'tested_by' and 'qa_approver' are required."),
            title=_("Incomplete Test Record")
        )

    if doc.tested_by == doc.qa_approver:
        frappe.throw(
            _("Segregation of Duties Violation: Tester cannot be the QA Approver."),
            title=_("Compliance Violation")
        )

    approver_roles = frappe.get_roles(doc.qa_approver)
    if "Quality Manager" not in approver_roles and "System Manager" not in approver_roles:
        frappe.throw(
            _("QA Approver '{0}' must have the 'Quality Manager' role.").format(doc.qa_approver),
            title=_("Unauthorized Approver")
        )

    session_user = frappe.session.user
    if session_user != doc.qa_approver and session_user != "Administrator":
        frappe.throw(
            _("Digest Signoff Violation: Only the designated QA Approver ({0}) can submit this approval.").format(doc.qa_approver),
            frappe.PermissionError
        )

    # Compute authentic SHA-256 Approval Integrity Digest
    doc.submitted_at = now_datetime()
    raw_payload = f"{doc.name}:{doc.task}:{doc.protocol_code}:{doc.result}:{doc.tested_by}:{session_user}:{doc.submitted_at}"
    digest = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
    doc.approval_signature = f"{session_user} [{doc.submitted_at}] Digest:{digest[:16]}"


def on_test_record_cancelled(doc, method=None):
    """
    Hook on tabEngineering Test Record 'on_cancel'.
    If the linked task was Completed:
    Demotes the task back to Started UNLESS a newer submitted Pass record exists for that protocol.
    """
    if not doc.task:
        return

    task_doc = frappe.get_doc("Task", doc.task)
    was_completed = (task_doc.custom_state_category == "Completed" or task_doc.status == "Completed")
    if not was_completed:
        return

    # Check if a qualifying Pass record exists that was created strictly AFTER this cancelled record
    newer_pass = frappe.db.sql(
        """
        SELECT name FROM `tabEngineering Test Record`
        WHERE task = %(task)s
          AND protocol_code = %(protocol_code)s
          AND docstatus = 1
          AND result = 'Pass'
          AND (creation > %(creation)s OR (creation = %(creation)s AND name > %(name)s))
        ORDER BY creation DESC, name DESC
        LIMIT 1
        """,
        {"task": doc.task, "protocol_code": doc.protocol_code, "creation": doc.creation, "name": doc.name}
    )

    if newer_pass:
        return

    # Demote task back to Started
    task_doc.custom_state_category = "Started"
    task_doc.status = "Working"
    task_doc.custom_needs_review = 1
    task_doc.flags.ignore_permissions = True
    task_doc.flags.in_qa_demotion = True
    task_doc.add_comment(
        "Workflow",
        _("COMPLIANCE ALERT: Test Record '{0}' (Protocol: {1}) was CANCELLED without newer replacement. Completed status revoked; task demoted to Started.").format(
            doc.name, doc.protocol_code
        )
    )
    task_doc.save()

    from hamerz_eoms.operations.rollover import _record_task_cycle_history
    _record_task_cycle_history(
        task=task_doc.name,
        from_cycle=task_doc.custom_cycle,
        to_cycle=task_doc.custom_cycle,
        rollover_count=task_doc.custom_rollover_count or 0,
        is_blocked=task_doc.custom_is_blocked or 0,
        event_type="Review Required",
        reason=f"Test record {doc.name} cancelled for protocol {doc.protocol_code}",
        execution_id=f"cancel_{doc.name}"
    )

    frappe.get_doc({
        "doctype": "ToDo",
        "description": _("Re-verify task {0}: Test record {1} was cancelled.").format(doc.task, doc.name),
        "reference_type": "Task",
        "reference_name": doc.task,
        "allocated_to": doc.qa_approver or frappe.session.user,
        "priority": "High"
    }).insert(ignore_permissions=True)


def backfill_task_state_categories():
    """
    Idempotent migration utility to backfill custom_state_category on pre-existing tasks.
    Safe to execute on staging or production benches without overwriting data.
    """
    frappe.db.sql("""
        UPDATE `tabTask`
        SET custom_state_category = CASE
            WHEN status = 'Completed' THEN 'Completed'
            WHEN status = 'Cancelled' THEN 'Cancelled'
            WHEN status IN ('Working', 'Pending Review') THEN 'Started'
            WHEN status = 'Template' THEN 'Backlog'
            ELSE 'Unstarted'
        END
        WHERE custom_state_category IS NULL OR custom_state_category = ''
    """)
    frappe.db.commit()

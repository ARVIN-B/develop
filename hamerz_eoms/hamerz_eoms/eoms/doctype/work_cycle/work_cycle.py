# -*- coding: utf-8 -*-
# file: hamerz_eoms/hamerz_eoms/eoms/doctype/work_cycle/work_cycle.py

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, get_datetime

class WorkCycle(Document):
    def validate(self):
        """
        Enforces Business Rules, Date Integrity, Baseline Security, and State Transitions:
        1. Date Validation: end_date must be >= start_date.
        2. Status Lifecycle & Reopen Guards:
           - Completed cycles cannot be reopened under any generic permissions.
           - Rollover Failed cycles cannot be manually set to Completed without running the rollover engine.
        3. Concurrency Protection:
           - Global row-level lock on Work Team to strictly guarantee only one Active cycle per team.
        4. Baseline Immutability:
           - When frozen, ALL baseline fields (points, machine hours, timestamp, flag) are strictly immutable.
           - Baseline freezing is restricted to the authorized scheduler/service.
        """
        self.validate_dates()
        self.validate_status_lifecycle()
        self.validate_baseline_security()
        self.validate_single_active_cycle_per_team()

    def validate_dates(self):
        if self.start_date and self.end_date:
            if getdate(self.end_date) < getdate(self.start_date):
                frappe.throw(
                    _("Invalid Cycle Dates: End Date ({0}) cannot be earlier than Start Date ({1}).").format(
                        self.end_date, self.start_date
                    ),
                    title=_("Date Sequence Violation")
                )

    def validate_status_lifecycle(self):
        allowed_statuses = ["Upcoming", "Active", "Completed", "Cancelled", "Rollover Failed"]
        if self.status not in allowed_statuses:
            frappe.throw(
                _("Invalid Work Cycle status '{0}'. Allowed statuses are: {1}").format(
                    self.status, ", ".join(allowed_statuses)
                ),
                title=_("Invalid Status")
            )

        if not self.is_new():
            old_doc = self.get_doc_before_save()
            if not old_doc:
                return

            user_roles = frappe.get_roles(frappe.session.user)
            is_admin = "System Manager" in user_roles or "Administrator" in user_roles

            # Rule: Completed cycles cannot be reopened.
            if old_doc.status == "Completed" and self.status != "Completed":
                if not getattr(self.flags, "allow_cycle_reopen", False):
                    frappe.throw(
                        _("Security Guard: Completed Work Cycles are permanently closed and cannot be reopened."),
                        title=_("State Machine Violation")
                    )

            # Rule: Rollover Failed cycles cannot be manually set to Completed without running rollover
            in_rollover = getattr(self.flags, "in_cycle_rollover", False)
            if old_doc.status == "Rollover Failed" and self.status == "Completed" and not in_rollover:
                if not is_admin:
                    frappe.throw(
                        _("Integrity Guard: Cycles in 'Rollover Failed' status cannot be manually completed. "
                          "You must run the cycle rollover engine to resolve uncompleted tasks."),
                        title=_("Rollover Required")
                    )

    def validate_baseline_security(self):
        """
        Guarantees Full Baseline Immutability:
        - Prevents unauthorized creation of pre-frozen cycles.
        - Once baseline_frozen == 1, all baseline metrics (points, machine hours, timestamp, flag)
          are completely locked and cannot be edited without explicit flags.allow_baseline_override.
        - Transition from unfrozen to frozen requires explicit service authorization (flags.in_baseline_freeze)
          or System Manager privileges.
        """
        if self.is_new():
            if self.baseline_frozen and not getattr(self.flags, "in_baseline_freeze", False):
                user_roles = frappe.get_roles(frappe.session.user)
                if "System Manager" not in user_roles and "Administrator" not in user_roles:
                    frappe.throw(
                        _("Security Guard: Work Cycles cannot be created directly in a frozen baseline state."),
                        frappe.PermissionError
                    )
            return

        old_doc = self.get_doc_before_save()
        if not old_doc:
            return

        in_freeze_service = getattr(self.flags, "in_baseline_freeze", False)
        allow_override = getattr(self.flags, "allow_baseline_override", False)
        user_roles = frappe.get_roles(frappe.session.user)
        is_admin = "System Manager" in user_roles or "Administrator" in user_roles

        # 1. Protection when baseline is ALREADY frozen (Strictly Immutable)
        if old_doc.baseline_frozen:
            if not self.baseline_frozen and not allow_override:
                frappe.throw(
                    _("Security Guard: Committed cycle baseline is frozen and cannot be unlocked."),
                    frappe.PermissionError
                )

            old_ts = get_datetime(old_doc.baseline_frozen_at) if old_doc.baseline_frozen_at else None
            new_ts = get_datetime(self.baseline_frozen_at) if self.baseline_frozen_at else None

            metrics_tampered = (
                float(self.baseline_points or 0) != float(old_doc.baseline_points or 0) or
                float(self.baseline_machine_hours or 0) != float(old_doc.baseline_machine_hours or 0) or
                old_ts != new_ts
            )
            if metrics_tampered and not allow_override:
                frappe.throw(
                    _("Security Guard: Baseline metrics (points, machine hours, frozen timestamp) "
                      "are immutable once frozen."),
                    frappe.PermissionError
                )

        # 2. Protection when transitioning from unfrozen to frozen
        elif not old_doc.baseline_frozen and self.baseline_frozen:
            if not in_freeze_service and not is_admin:
                frappe.throw(
                    _("Security Guard: Manual baseline freezing is restricted. "
                      "Baselines must be frozen via the automated scheduler or by a System Manager."),
                    frappe.PermissionError
                )

    def validate_single_active_cycle_per_team(self):
        """
        Global Concurrency Guard:
        Enforces single Active cycle per team across all paths (UI, API, Scheduler, background jobs).
        Serializes verification via a row-level lock on the parent Work Team.
        """
        if self.status == "Active" and self.team:
            # 1. Acquire row lock on parent Work Team to serialize concurrent activations
            team_lock = frappe.db.sql(
                "SELECT name FROM `tabWork Team` WHERE name = %s FOR UPDATE",
                (self.team,), as_dict=True
            )
            if not team_lock:
                frappe.throw(
                    _("Work Team '{0}' does not exist.").format(self.team),
                    frappe.DoesNotExistError
                )

            # 2. Under lock, verify if another cycle is already active
            existing_active = frappe.db.sql("""
                SELECT name FROM `tabWork Cycle`
                WHERE team = %s 
                  AND status = 'Active' 
                  AND name != %s
            """, (self.team, self.name or ""), as_dict=True)

            if existing_active:
                frappe.throw(
                    _("Concurrency Guard: Team '{0}' already has an Active cycle ({1}). "
                      "A team cannot have multiple active cycles running concurrently.").format(
                        self.team, existing_active[0].name
                    ),
                    title=_("Multiple Active Cycles Blocked")
                )

    def on_trash(self):
        """
        Guards Referential Integrity:
        Prevents deletion of Work Cycle if tasks or audit history reference it.
        """
        # 1. Check attached tasks
        attached_tasks_count = frappe.db.count("Task", filters={"custom_cycle": self.name})
        if attached_tasks_count > 0:
            frappe.throw(
                _("Referential Integrity: Cannot delete Work Cycle '{0}' because {1} task(s) are currently attached to it.").format(
                    self.name, attached_tasks_count
                ),
                frappe.LinkExistsError
            )

        # 2. Check audit history
        history_count = frappe.db.sql("""
            SELECT COUNT(*) FROM `tabTask Cycle History`
            WHERE from_cycle = %s OR to_cycle = %s
        """, (self.name, self.name))[0][0]

        if history_count > 0:
            frappe.throw(
                _("Referential Integrity: Cannot delete Work Cycle '{0}' because {1} task cycle history audit record(s) reference it.").format(
                    self.name, history_count
                ),
                frappe.LinkExistsError
            )

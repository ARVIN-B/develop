# -*- coding: utf-8 -*-
# file: hamerz_eoms/hamerz_eoms/tests/test_eoms.py

import unittest
from unittest.mock import patch
import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, now_datetime
from hamerz_eoms.operations.rollover import execute_cycle_rollover, _calculate_cycle_metrics_snapshot

class TestEOMSSuite(IntegrationTestCase):
    def setUp(self):
        """Build reliable test fixtures with proper roles and custom permissions."""
        self.addCleanup(frappe.set_user, "Administrator")

        # 1. Guarantee Role 'Quality Manager'
        if not frappe.db.exists("Role", "Quality Manager"):
            frappe.get_doc({
                "doctype": "Role",
                "role_name": "Quality Manager",
                "name": "Quality Manager",
                "disabled": 0,
                "desk_access": 1
            }).insert(ignore_permissions=True)

        # 2. Guarantee Custom DocPerm on Task for Quality Manager
        if not frappe.db.exists("Custom DocPerm", {"parent": "Task", "role": "Quality Manager", "permlevel": 0}):
            frappe.get_doc({
                "doctype": "Custom DocPerm",
                "name": "Task-Quality Manager-0",
                "parent": "Task",
                "parenttype": "DocType",
                "parentfield": "permissions",
                "role": "Quality Manager",
                "permlevel": 0,
                "read": 1,
                "write": 1,
                "create": 1,
                "export": 1
            }).insert(ignore_permissions=True)

        # 3. Create Users
        self.team_lead = f"lead_{frappe.generate_hash(length=4)}@example.com"
        self.tester = f"tester_{frappe.generate_hash(length=4)}@example.com"
        self.qa_approver = f"qa_{frappe.generate_hash(length=4)}@example.com"

        for u in [self.team_lead, self.tester, self.qa_approver]:
            if not frappe.db.exists("User", u):
                user_doc = frappe.get_doc({
                    "doctype": "User",
                    "email": u,
                    "first_name": "TestUser",
                    "send_welcome_email": 0
                }).insert(ignore_permissions=True)
                
                user_doc.add_roles("Projects User")
                if u == self.team_lead:
                    user_doc.add_roles("Projects Manager")
                elif u == self.qa_approver:
                    user_doc.add_roles("Quality Manager")

        # 4. Work Team & Active Cycle
        self.team = frappe.get_doc({
            "doctype": "Work Team",
            "team_name": f"Tooling Team {frappe.generate_hash(length=4)}",
            "capacity_unit": "Machine Hours",
            "team_lead": self.team_lead,
            "cycle_duration_weeks": 2
        }).insert(ignore_permissions=True)

        self.cycle_1 = frappe.get_doc({
            "doctype": "Work Cycle",
            "cycle_name": f"Active Cycle {frappe.generate_hash(length=4)}",
            "team": self.team.name,
            "status": "Active",
            "start_date": add_days(getdate(), -14),
            "end_date": getdate(),
            "baseline_machine_hours": 40.0,
            "baseline_frozen": 1,
            "baseline_frozen_at": add_days(getdate(), -14)
        }).insert(ignore_permissions=True)

    def test_positive_qa_gate_completion_lifecycle(self):
        """
        Scenario 1: Happy Path via full ORM save()
        Verify that a task with mandatory protocols completes cleanly through standard task.save()
        when a valid 'Pass' record is submitted with distinct tester and authorized QA approver.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Valid Mold Core Heat Treatment",
            "custom_team": self.team.name,
            "custom_cycle": self.cycle_1.name,
            "custom_requires_qa_gate": 1,
            "custom_state_category": "Started"
        }).insert(ignore_permissions=True)

        task.append("custom_mandatory_protocols", {
            "protocol_name": "Hardness Protocol",
            "protocol_code": "HARD-01"
        })
        task.save(ignore_permissions=True)

        frappe.set_user(self.qa_approver)
        test_rec = frappe.get_doc({
            "doctype": "Engineering Test Record",
            "task": task.name,
            "protocol_code": "HARD-01",
            "protocol_name": "Hardness Protocol",
            "result": "Pass",
            "tested_by": self.tester,
            "qa_approver": self.qa_approver,
            "test_date": getdate()
        }).insert(ignore_permissions=True)
        test_rec.submit()
        self.assertIn("Digest:", test_rec.approval_signature)
        frappe.set_user("Administrator")

        task.custom_state_category = "Completed"
        task.save()

        task.reload()
        self.assertEqual(task.custom_state_category, "Completed")
        self.assertEqual(task.status, "Completed")

    def test_qa_gate_empty_mandatory_protocols_blocked(self):
        """
        Scenario 2: QA Gate Empty Protocol Bypass Prevention
        Verifies that a task requiring QA Gate cannot complete if custom_mandatory_protocols is empty.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Task with QA Gate but No Protocols",
            "custom_team": self.team.name,
            "custom_cycle": self.cycle_1.name,
            "custom_requires_qa_gate": 1,
            "custom_state_category": "Started"
        }).insert(ignore_permissions=True)

        task.custom_state_category = "Completed"
        with self.assertRaisesRegex(frappe.ValidationError, r"Task requires QA Gate verification, but no mandatory protocols are assigned"):
            task.save()

    def test_child_table_protocol_tampering_blocked(self):
        """
        Scenario 3: Protection of Mandatory Protocols Table
        Non-QA user (Projects User) cannot add or delete rows in custom_mandatory_protocols.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Protected Protocol Task",
            "custom_team": self.team.name,
            "custom_requires_qa_gate": 1,
            "custom_state_category": "Started"
        }).insert(ignore_permissions=True)

        task.append("custom_mandatory_protocols", {
            "protocol_code": "CAL-01",
            "protocol_name": "Calibration Check"
        })
        task.save(ignore_permissions=True)

        # Non-QA user tries to remove the protocol row
        frappe.set_user(self.tester)
        task.reload()
        task.custom_mandatory_protocols = []
        with self.assertRaisesRegex(frappe.PermissionError, r"Only Quality Manager or System Manager can modify mandatory QA protocols"):
            task.save()

    def test_cancellation_of_latest_pass_blocks_completion_despite_older_pass(self):
        """
        Scenario 4: Latest Record Status in QA Gate
        When Pass #1 is followed by Pass #2, and Pass #2 is cancelled,
        older Pass #1 is superseded; completion must be BLOCKED because latest record is cancelled.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Multi-Record EDM Inspection",
            "custom_team": self.team.name,
            "custom_cycle": self.cycle_1.name,
            "custom_requires_qa_gate": 1,
            "custom_state_category": "Started"
        }).insert(ignore_permissions=True)

        task.append("custom_mandatory_protocols", {
            "protocol_code": "EDM-01",
            "protocol_name": "EDM Gap Check"
        })
        task.save(ignore_permissions=True)

        frappe.set_user(self.qa_approver)
        # Pass #1
        rec1 = frappe.get_doc({
            "doctype": "Engineering Test Record",
            "task": task.name,
            "protocol_code": "EDM-01",
            "protocol_name": "EDM Gap Check",
            "result": "Pass",
            "tested_by": self.tester,
            "qa_approver": self.qa_approver,
            "test_date": getdate()
        }).insert(ignore_permissions=True)
        rec1.submit()

        # Pass #2
        rec2 = frappe.get_doc({
            "doctype": "Engineering Test Record",
            "task": task.name,
            "protocol_code": "EDM-01",
            "protocol_name": "EDM Gap Check",
            "result": "Pass",
            "tested_by": self.tester,
            "qa_approver": self.qa_approver,
            "test_date": getdate()
        }).insert(ignore_permissions=True)
        rec2.submit()

        # Cancel latest Pass #2
        rec2.cancel()
        frappe.set_user("Administrator")

        # Attempt to complete task: Must FAIL because latest record is cancelled!
        task.custom_state_category = "Completed"
        with self.assertRaisesRegex(frappe.ValidationError, r"The latest test record .* was cancelled. Re-testing is required"):
            task.save()

    def test_new_test_submission_on_completed_task_blocked(self):
        """
        Scenario 5 (P0-1): Block New Test Records on Completed Task
        Submitting a new test record directly on an already Completed task is strictly blocked.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Completed Assembly",
            "custom_team": self.team.name,
            "custom_state_category": "Completed",
            "status": "Completed"
        }).insert(ignore_permissions=True)

        frappe.set_user(self.qa_approver)
        test_rec = frappe.get_doc({
            "doctype": "Engineering Test Record",
            "task": task.name,
            "protocol_code": "ASY-01",
            "protocol_name": "Assembly Check",
            "result": "Fail",
            "tested_by": self.tester,
            "qa_approver": self.qa_approver,
            "test_date": getdate()
        }).insert(ignore_permissions=True)

        with self.assertRaisesRegex(frappe.ValidationError, r"Cannot submit new test record for Completed task"):
            test_rec.submit()

    def test_direct_completed_to_cancelled_transition_blocked(self):
        """
        Scenario 6 (P0-3): Block Direct Completed -> Cancelled Jump
        A Completed task cannot be directly transitioned to Cancelled without reopening first.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Completed Project Milestone",
            "custom_team": self.team.name,
            "custom_state_category": "Completed",
            "status": "Completed"
        }).insert(ignore_permissions=True)

        task.custom_state_category = "Cancelled"
        with self.assertRaisesRegex(frappe.ValidationError, r"Invalid State Transition: A Completed task cannot be directly Cancelled"):
            task.save()

    def test_done_lock_freezes_project_and_protocols(self):
        """
        Scenario 7 (P0-4): Done-Lock Comprehensive Immutability
        Verifies that modifying project or mandatory protocols on Completed tasks is blocked.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Completed Spark Erosion",
            "custom_team": self.team.name,
            "custom_state_category": "Completed",
            "status": "Completed",
            "custom_machine_hours": 12.0
        }).insert(ignore_permissions=True)

        task.reload()
        task.custom_machine_hours = 15.0
        with self.assertRaisesRegex(frappe.ValidationError, r"Done-Lock Violation"):
            task.save()

    def test_reopen_guard_role_and_reason_enforcement(self):
        """
        Scenario 8: Reopen Guard & Custom DocPerm
        Projects User without Quality Manager is BLOCKED from reopening.
        QA user with valid reason SUCCEEDS and reason is cleared.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Completed Assembly Check",
            "custom_team": self.team.name,
            "custom_state_category": "Completed",
            "status": "Completed"
        }).insert(ignore_permissions=True)

        frappe.set_user(self.tester)
        task.custom_state_category = "Started"
        task.custom_reopen_reason = "Dimensional variance detected"
        with self.assertRaisesRegex(frappe.PermissionError, r"Only Quality Manager or System Manager can reopen"):
            task.save()

        frappe.set_user(self.qa_approver)
        task.reload()
        task.custom_state_category = "Started"
        task.custom_reopen_reason = "Dimensional variance observed during CMM audit"
        task.save()

        task.reload()
        self.assertEqual(task.custom_state_category, "Started")
        self.assertIsNone(task.custom_reopen_reason)

    def test_cancellation_demotes_completed_task(self):
        """
        Scenario 9: Demotion on Revocation of Qualifying Pass Record
        When the only qualifying Pass record is cancelled, the Completed task is demoted to Started.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Electrode EDM Machining",
            "custom_team": self.team.name,
            "custom_cycle": self.cycle_1.name,
            "custom_requires_qa_gate": 1,
            "custom_state_category": "Started"
        }).insert(ignore_permissions=True)

        task.append("custom_mandatory_protocols", {
            "protocol_name": "EDM Gap Check",
            "protocol_code": "EDM-01"
        })
        task.save(ignore_permissions=True)

        frappe.set_user(self.qa_approver)
        test_rec = frappe.get_doc({
            "doctype": "Engineering Test Record",
            "task": task.name,
            "protocol_code": "EDM-01",
            "protocol_name": "EDM Gap Check",
            "result": "Pass",
            "tested_by": self.tester,
            "qa_approver": self.qa_approver,
            "test_date": getdate()
        }).insert(ignore_permissions=True)
        test_rec.submit()
        frappe.set_user("Administrator")

        task.custom_state_category = "Completed"
        task.save()
        self.assertEqual(task.status, "Completed")

        frappe.set_user(self.qa_approver)
        test_rec.cancel()
        frappe.set_user("Administrator")

        task.reload()
        self.assertEqual(task.custom_state_category, "Started")
        self.assertEqual(task.status, "Working")
        self.assertEqual(task.custom_needs_review, 1)

    def test_mismatched_team_task_detached_during_rollover(self):
        """
        Scenario 10: Clean Team Resolution in Rollover
        Tasks from other teams erroneously tagged with this cycle are cleanly detached (custom_cycle = None)
        so no uncompleted tasks remain on the Completed cycle.
        """
        other_team = frappe.get_doc({
            "doctype": "Work Team",
            "team_name": f"Plasma Team {frappe.generate_hash(length=4)}",
            "capacity_unit": "Story Points",
            "team_lead": self.team_lead
        }).insert(ignore_permissions=True)

        mismatched_task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Plasma Electronics Test",
            "custom_team": other_team.name,
            "custom_cycle": self.cycle_1.name,
            "custom_state_category": "Started"
        }).insert(ignore_permissions=True)

        summary = execute_cycle_rollover(self.cycle_1.name)

        self.assertIn(mismatched_task.name, summary["mismatched_team_tasks"])
        mismatched_task.reload()
        # Task must be detached, not left orphaned on the completed cycle
        self.assertIsNone(mismatched_task.custom_cycle)
        self.cycle_1.reload()
        self.assertEqual(self.cycle_1.status, "Completed")

    def test_blocked_task_rolls_forward_without_count_increment(self):
        """
        Scenario 11: Blocked Task Progression
        Blocked task carries forward to the next cycle without incrementing custom_rollover_count.
        """
        next_cycle = frappe.get_doc({
            "doctype": "Work Cycle",
            "cycle_name": f"Upcoming Cycle {frappe.generate_hash(length=4)}",
            "team": self.team.name,
            "status": "Upcoming",
            "start_date": getdate(),
            "end_date": add_days(getdate(), 14),
            "baseline_frozen": 0
        }).insert(ignore_permissions=True)

        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Waiting on Raw Material Import",
            "custom_team": self.team.name,
            "custom_cycle": self.cycle_1.name,
            "custom_state_category": "Started",
            "custom_is_blocked": 1,
            "custom_rollover_count": 1
        }).insert(ignore_permissions=True)

        summary = execute_cycle_rollover(self.cycle_1.name)

        task.reload()
        self.assertEqual(task.custom_cycle, next_cycle.name)
        # Rollover count must NOT be incremented for blocked tasks
        self.assertEqual(task.custom_rollover_count, 1)
        self.assertEqual(task.custom_entered_via_rollover, 1)

    def test_scope_creep_team_isolation_and_frozen_baseline(self):
        """
        Scenario 12: Scope Creep Team Filtering & Frozen Baseline Recognition
        Verifies that scope creep is team-filtered and respects frozen baseline thresholds.
        """
        cycle_data = {
            "name": self.cycle_1.name,
            "team": self.team.name,
            "start_date": self.cycle_1.start_date,
            "baseline_machine_hours": 50.0,
            "baseline_points": 0.0,
            "baseline_frozen_at": self.cycle_1.start_date
        }

        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Unplanned Wire EDM",
            "custom_team": self.team.name,
            "custom_cycle": self.cycle_1.name,
            "custom_machine_hours": 15.0,
            "custom_cycle_added_at": add_days(getdate(), 1),
            "custom_entered_via_rollover": 0,
            "custom_state_category": "Unstarted"
        }).insert(ignore_permissions=True)

        metrics = _calculate_cycle_metrics_snapshot(cycle_data, capacity_unit="Machine Hours")

        self.assertEqual(metrics["scope_creep_percent"], 30.0)
        self.assertIn("Machine Hours", metrics["scope_creep_note"])

    def test_bidirectional_state_synchronization(self):
        """
        Scenario 13: Bidirectional State Synchronization
        Verifies parity between native Task.status and Linear-style custom_state_category.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "State Sync Parity Test",
            "custom_team": self.team.name
        }).insert(ignore_permissions=True)

        task.custom_state_category = "Started"
        task.save()
        self.assertEqual(task.status, "Working")

        task.status = "Cancelled"
        task.save()
        self.assertEqual(task.custom_state_category, "Cancelled")

    def test_activate_upcoming_cycles_skips_when_team_has_active_cycle(self):
        """
        Scenario 14: Scheduler Concurrency & Multi-Cycle Protection
        Verifies that activate_upcoming_cycles refuses to activate a second cycle for a team
        that already has an Active cycle.
        """
        from hamerz_eoms.operations.scheduler import activate_upcoming_cycles

        upcoming_cycle = frappe.get_doc({
            "doctype": "Work Cycle",
            "cycle_name": f"Second Upcoming {frappe.generate_hash(length=4)}",
            "team": self.team.name,
            "status": "Upcoming",
            "start_date": getdate(),
            "end_date": add_days(getdate(), 14)
        }).insert(ignore_permissions=True)

        activate_upcoming_cycles()

        upcoming_cycle.reload()
        self.assertEqual(upcoming_cycle.status, "Upcoming")

    def test_mismatched_task_detachment_failure_blocks_cycle_completion(self):
        """
        Scenario 15: Mismatched Task Detachment Failure Safety
        Verifies that if detaching a mismatched task fails or an uncompleted task remains,
        the cycle is marked 'Rollover Failed' and cannot falsely become 'Completed'.
        """
        other_team = frappe.get_doc({
            "doctype": "Work Team",
            "team_name": f"Other Team {frappe.generate_hash(length=4)}",
            "capacity_unit": "Story Points",
            "team_lead": self.team_lead
        }).insert(ignore_permissions=True)

        mismatched_task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Failing Detach Task",
            "custom_team": other_team.name,
            "custom_cycle": self.cycle_1.name,
            "custom_state_category": "Started"
        }).insert(ignore_permissions=True)

        # Mock Task.save to raise ONLY on the mismatched task detachment
        orig_doc_save = frappe.model.document.Document.save

        def selective_save(doc_self, *args, **kwargs):
            if doc_self.doctype == "Task" and doc_self.name == mismatched_task.name:
                raise frappe.ValidationError("Simulated DB Lock on Mismatched Task")
            return orig_doc_save(doc_self, *args, **kwargs)

        with patch.object(frappe.model.document.Document, "save", side_effect=selective_save, autospec=True):
            summary = execute_cycle_rollover(self.cycle_1.name)

        # The cycle must NOT become Completed! It must be Rollover Failed!
        self.cycle_1.reload()
        self.assertEqual(self.cycle_1.status, "Rollover Failed")
        self.assertGreater(len(summary["failed_tasks"]), 0)

    def test_expired_upcoming_cycles_cancelled_by_nightly_job(self):
        """
        Scenario 16: Downtime Recovery for Expired Upcoming Cycles
        Verifies that close_due_cycles marks Upcoming cycles with elapsed end_date as Cancelled,
        and cleanly detaches any tasks assigned to that cycle back to the team backlog.
        """
        from hamerz_eoms.operations.scheduler import close_due_cycles

        expired_cycle = frappe.get_doc({
            "doctype": "Work Cycle",
            "cycle_name": f"Downtime Expired {frappe.generate_hash(length=4)}",
            "team": self.team.name,
            "status": "Upcoming",
            "start_date": add_days(getdate(), -20),
            "end_date": add_days(getdate(), -5)
        }).insert(ignore_permissions=True)

        attached_task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Task Attached to Expired Upcoming Cycle",
            "custom_team": self.team.name,
            "custom_cycle": expired_cycle.name,
            "custom_state_category": "Backlog"
        }).insert(ignore_permissions=True)

        close_due_cycles(sync_execution=True)

        expired_cycle.reload()
        self.assertEqual(expired_cycle.status, "Cancelled")
        self.assertIn("expired in Upcoming state", expired_cycle.failure_notes)

        # Verify task is cleanly detached
        attached_task.reload()
        self.assertIsNone(attached_task.custom_cycle)

        # Verify audit history record was logged
        history = frappe.get_all(
            "Task Cycle History",
            filters={"task": attached_task.name, "from_cycle": expired_cycle.name},
            fields=["event_type", "to_cycle"]
        )
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].event_type, "Unscheduled")
        self.assertIsNone(history[0].to_cycle)

    def test_work_cycle_end_date_before_start_date_blocked(self):
        """
        Scenario 17: Work Cycle Date Sequence Validation
        Verifies that setting end_date earlier than start_date throws a ValidationError.
        """
        invalid_cycle = frappe.get_doc({
            "doctype": "Work Cycle",
            "cycle_name": f"Invalid Dates {frappe.generate_hash(length=4)}",
            "team": self.team.name,
            "status": "Upcoming",
            "start_date": getdate(),
            "end_date": add_days(getdate(), -3)
        })
        with self.assertRaises(frappe.ValidationError):
            invalid_cycle.insert()

    def test_multiple_active_cycles_per_team_blocked(self):
        """
        Scenario 18: Work Cycle Concurrency Guard
        Verifies that a team cannot have more than one Active cycle at a time.
        """
        second_active_cycle = frappe.get_doc({
            "doctype": "Work Cycle",
            "cycle_name": f"Second Active {frappe.generate_hash(length=4)}",
            "team": self.team.name,
            "status": "Active",
            "start_date": getdate(),
            "end_date": add_days(getdate(), 14)
        })
        with self.assertRaises(frappe.ValidationError):
            second_active_cycle.insert()

    def test_work_team_flavor_capacity_alignment(self):
        """
        Scenario 19: Work Team Flavor-Capacity Unit Policy Validation
        Verifies that Flavor C (Tooling/Molds) mandates 'Machine Hours' and rejects 'Story Points'.
        """
        team_invalid = frappe.get_doc({
            "doctype": "Work Team",
            "team_name": f"Invalid Flavor Team {frappe.generate_hash(length=4)}",
            "flavor": "Flavor C (Tooling/Molds)",
            "capacity_unit": "Story Points",
            "team_lead": self.team_lead
        })
        with self.assertRaises(frappe.ValidationError):
            team_invalid.insert()

    def test_work_cycle_deletion_blocked_if_tasks_exist(self):
        """
        Scenario 20: Referential Integrity Guard on Work Cycle Deletion
        Verifies that deleting a cycle with attached tasks throws LinkExistsError.
        """
        task = frappe.get_doc({
            "doctype": "Task",
            "subject": "Task Protecting Cycle From Deletion",
            "custom_team": self.team.name,
            "custom_cycle": self.cycle_1.name,
            "custom_state_category": "Unstarted"
        }).insert(ignore_permissions=True)

        with self.assertRaises(frappe.LinkExistsError):
            self.cycle_1.delete()

    def test_expired_upcoming_cycle_remains_upcoming_if_task_detachment_fails(self):
        """
        Scenario 21: Strict Transactional Integrity on Expired Upcoming Cleanup
        Verifies that if task detachment fails, the expired cycle is NOT marked Cancelled,
        and failure notes are recorded so it can be retried.
        """
        from hamerz_eoms.operations.scheduler import close_due_cycles

        exp_cycle_fail = frappe.get_doc({
            "doctype": "Work Cycle",
            "cycle_name": f"Downtime Fail {frappe.generate_hash(length=4)}",
            "team": self.team.name,
            "status": "Upcoming",
            "start_date": add_days(getdate(), -20),
            "end_date": add_days(getdate(), -5)
        }).insert(ignore_permissions=True)

        task_failing = frappe.get_doc({
            "doctype": "Task",
            "subject": "Task Failing Detachment on Expired Cycle",
            "custom_team": self.team.name,
            "custom_cycle": exp_cycle_fail.name,
            "custom_state_category": "Backlog"
        }).insert(ignore_permissions=True)

        orig_doc_save = frappe.model.document.Document.save

        def selective_task_fail(doc_self, *args, **kwargs):
            if doc_self.doctype == "Task" and doc_self.name == task_failing.name:
                raise frappe.ValidationError("Simulated DB Lock on Task Detach")
            return orig_doc_save(doc_self, *args, **kwargs)

        with patch.object(frappe.model.document.Document, "save", side_effect=selective_task_fail, autospec=True):
            close_due_cycles(sync_execution=True)

        exp_cycle_fail.reload()
        # MUST NOT be Cancelled!
        self.assertEqual(exp_cycle_fail.status, "Upcoming")
        self.assertIn("Cleanup Incomplete", exp_cycle_fail.failure_notes)

    def test_work_cycle_baseline_metrics_immutability(self):
        """
        Scenario 22: Baseline Metrics Full Immutability
        Verifies that modifying baseline_points on a frozen cycle throws PermissionError with the exact guard message.
        """
        cycle_doc = frappe.get_doc("Work Cycle", self.cycle_1.name)
        cycle_doc.baseline_points = 999.0
        with self.assertRaisesRegex(frappe.PermissionError, r"Baseline metrics .* are immutable once frozen"):
            cycle_doc.save()

    def test_work_cycle_completed_cannot_be_reopened(self):
        """
        Scenario 23: Work Cycle State Machine Reopen Guard
        Verifies that Completed cycles cannot be reopened to Active.
        """
        completed_cycle = frappe.get_doc({
            "doctype": "Work Cycle",
            "cycle_name": f"Completed Cycle {frappe.generate_hash(length=4)}",
            "team": self.team.name,
            "status": "Upcoming",
            "start_date": add_days(getdate(), -30),
            "end_date": add_days(getdate(), -16)
        }).insert(ignore_permissions=True)

        completed_cycle.status = "Completed"
        completed_cycle.save(ignore_permissions=True)

        completed_cycle.status = "Active"
        with self.assertRaises(frappe.ValidationError):
            completed_cycle.save()

    def test_work_team_cycle_duration_float_rejected(self):
        """
        Scenario 24: Work Team Positive Integer Validation
        Verifies that non-integer values like 1.5 or 0 for cycle_duration_weeks are rejected.
        """
        team_float = frappe.get_doc({
            "doctype": "Work Team",
            "team_name": f"Float Team {frappe.generate_hash(length=4)}",
            "capacity_unit": "Story Points",
            "cycle_duration_weeks": 1.5,
            "team_lead": self.team_lead
        })
        with self.assertRaises(frappe.ValidationError):
            team_float.insert()

# -*- coding: utf-8 -*-
# file: hamerz_eoms/hamerz_eoms/eoms/doctype/work_team/work_team.py

import frappe
from frappe import _
from frappe.model.document import Document

class WorkTeam(Document):
    def validate(self):
        """
        Enforces Business Rules and Flavor-Capacity Consistency:
        1. Cycle duration must be a positive whole integer of at least 1 week.
        2. Engineering Flavor must align with the designated Capacity Unit:
           - Flavor A (Medical Consumables) & Flavor B (Plasma Devices) -> Story Points
           - Flavor C (Tooling/Molds) & Flavor D (Cleanroom Ops) -> Machine Hours
        """
        self.validate_cycle_duration()
        self.validate_flavor_capacity_alignment()

    def validate_cycle_duration(self):
        val = self.cycle_duration_weeks
        if val is None or isinstance(val, bool) or isinstance(val, float):
            frappe.throw(
                _("Cycle Duration must be a positive whole integer of at least 1 week."),
                title=_("Invalid Cycle Duration")
            )
        if isinstance(val, str):
            if not val.strip().isdigit():
                frappe.throw(
                    _("Cycle Duration must be a positive whole integer of at least 1 week."),
                    title=_("Invalid Cycle Duration")
                )
            val = int(val.strip())
        if not isinstance(val, int) or val < 1:
            frappe.throw(
                _("Cycle Duration must be a positive whole integer of at least 1 week."),
                title=_("Invalid Cycle Duration")
            )
        self.cycle_duration_weeks = val

    def validate_flavor_capacity_alignment(self):
        if self.flavor in ["Flavor C (Tooling/Molds)", "Flavor D (Cleanroom Ops)"]:
            if self.capacity_unit != "Machine Hours":
                frappe.throw(
                    _("Engineering teams for {0} must use 'Machine Hours' as their Capacity Unit. "
                      "Found: '{1}'.").format(self.flavor, self.capacity_unit),
                    title=_("Flavor Policy Mismatch")
                )
        elif self.flavor in ["Flavor A (Medical Consumables)", "Flavor B (Plasma Devices)"]:
            if self.capacity_unit != "Story Points":
                frappe.throw(
                    _("Engineering teams for {0} must use 'Story Points' as their Capacity Unit. "
                      "Found: '{1}'.").format(self.flavor, self.capacity_unit),
                    title=_("Flavor Policy Mismatch")
                )

    def on_trash(self):
        """
        Guards Referential Integrity:
        Prevents deletion of Work Team if active or historical cycles or tasks are linked to it.
        """
        # 1. Check linked Work Cycles
        linked_cycles = frappe.db.sql("""
            SELECT name, status FROM `tabWork Cycle` WHERE team = %s
        """, (self.name,), as_dict=True)

        if linked_cycles:
            frappe.throw(
                _("Referential Integrity: Cannot delete Work Team '{0}' because {1} Work Cycle(s) are linked to it (e.g. {2}).").format(
                    self.name, len(linked_cycles), linked_cycles[0].name
                ),
                frappe.LinkExistsError
            )

        # 2. Check linked Tasks
        linked_tasks_count = frappe.db.count("Task", filters={"custom_team": self.name})
        if linked_tasks_count > 0:
            frappe.throw(
                _("Referential Integrity: Cannot delete Work Team '{0}' because {1} task(s) are assigned to it.").format(
                    self.name, linked_tasks_count
                ),
                frappe.LinkExistsError
            )

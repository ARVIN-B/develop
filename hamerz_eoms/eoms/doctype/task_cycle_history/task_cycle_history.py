# -*- coding: utf-8 -*-
import frappe
from frappe import _
from frappe.model.document import Document

class TaskCycleHistory(Document):
    def validate(self):
        # Strict Append-Only constraint: Disallow editing existing historical records
        if not self.is_new() and not getattr(self.flags, "ignore_immutable_guard", False):
            frappe.throw(_("Task Cycle History records are strictly immutable and cannot be updated."), frappe.PermissionError)

    def on_trash(self):
        # Disallow deleting history logs unless explicitly running in test mode
        if not getattr(self.flags, "ignore_immutable_guard", False):
            frappe.throw(_("Task Cycle History records cannot be deleted."), frappe.PermissionError)

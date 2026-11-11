# -*- coding: utf-8 -*-
# file: hamerz_eoms/hamerz_eoms/install.py

import frappe

def before_install():
    """
    Executes BEFORE doctype schemas are synced (sync_for).
    Guarantees that Role 'Quality Manager' exists before Frappe validates
    DocPerms defined in engineering_test_record.json.
    """
    if not frappe.db.exists("Role", "Quality Manager"):
        frappe.get_doc({
            "doctype": "Role",
            "role_name": "Quality Manager",
            "name": "Quality Manager",
            "disabled": 0,
            "desk_access": 1
        }).insert(ignore_permissions=True)
        frappe.db.commit()


def after_install():
    """
    Executes AFTER installation to guarantee Custom DocPerm on Task
    for Quality Manager is created.
    """
    # 1. Fallback verification for Role
    before_install()

    # 2. Grant Custom DocPerm on Task for Quality Manager
    perm_exists = frappe.db.exists(
        "Custom DocPerm",
        {"parent": "Task", "role": "Quality Manager", "permlevel": 0}
    )
    if not perm_exists:
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
            "delete": 0,
            "submit": 0,
            "cancel": 0,
            "amend": 0,
            "export": 1,
            "print": 1,
            "email": 1,
            "report": 1,
            "share": 1
        }).insert(ignore_permissions=True)
        frappe.db.commit()

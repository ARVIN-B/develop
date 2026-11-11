# -*- coding: utf-8 -*-
# file: hamerz_eoms/hamerz_eoms/hooks.py

app_name = "hamerz_eoms"
app_title = "Hamerz EOMS"
app_publisher = "Solar Electric Energy Co. / Hamerz Plasma"
app_description = "Agile Project Operations & Engineering Quality Gates on Frappe/ERPNext v16"
app_version = "1.4.6"

# Installation Hooks
before_install = "hamerz_eoms.install.before_install"
after_install = "hamerz_eoms.install.after_install"

# Document Event Hooks
doc_events = {
    "Task": {
        "before_validate": "hamerz_eoms.quality.qa_gate.on_task_before_validate",
        "validate": [
            "hamerz_eoms.quality.qa_gate.validate_task_security_and_done_lock",
            "hamerz_eoms.quality.qa_gate.validate_task_qa_gate",
        ],
    },
    "Project": {
        "validate": "hamerz_eoms.quality.qa_gate.validate_project_qa_gate_toggle",
    },
    "Engineering Test Record": {
        "before_submit": "hamerz_eoms.quality.qa_gate.validate_test_record_submission",
        "on_cancel": "hamerz_eoms.quality.qa_gate.on_test_record_cancelled",
    },
}

# Accurate Cron-Based Scheduler Events
# Note: Executed based on Frappe system timezone (e.g. Asia/Tehran)
scheduler_events = {
    "cron": {
        # Nightly at 23:55 - Auto-closes active and retry-failed cycles whose end date has arrived
        "55 23 * * *": [
            "hamerz_eoms.operations.scheduler.close_due_cycles"
        ],
        # Saturday at 06:00 - Activates upcoming cycles for the new work period
        "0 6 * * 6": [
            "hamerz_eoms.operations.scheduler.activate_upcoming_cycles"
        ],
        # Saturday at 14:00 - Freezes baseline scope for newly active cycles
        "0 14 * * 6": [
            "hamerz_eoms.operations.scheduler.freeze_cycle_baselines"
        ],
    }
}

# Export Fixtures for bench migrate (Explicit fieldnames to avoid SQL LIKE wildcard hazards)
fixtures = [
    {
        "dt": "Role",
        "filters": [["name", "in", ["Quality Manager"]]]
    },
    {
        "dt": "Custom DocPerm",
        "filters": [["parent", "=", "Task"], ["role", "=", "Quality Manager"]]
    },
    {
        "dt": "Custom Field",
        "filters": [
            ["dt", "in", ["Task", "Project"]],
            ["fieldname", "in", [
                "custom_team", "custom_cycle", "custom_task_lead",
                "custom_state_category", "custom_story_points",
                "custom_machine_hours", "custom_rollover_count",
                "custom_entered_via_rollover", "custom_is_blocked",
                "custom_blocked_reason", "custom_needs_review",
                "custom_reopen_reason", "custom_requires_qa_gate",
                "custom_cycle_added_at", "custom_mandatory_protocols",
                "custom_flavor"
            ]]
        ]
    }
]

"""
Anti-Ragging Portal - Dedicated Background Reminder Scheduler Worker

In production deployments with multi-worker WSGI servers (e.g. Gunicorn with multiple
worker processes), each worker would spawn its own reminder thread if in-app scheduling
is enabled.

This standalone worker runs as a dedicated singleton process to reliably process
pending-action reminders without race conditions or duplicated notifications.

Usage:
    python scripts/run_scheduler.py
    # or
    python -m scripts.run_scheduler

Environment Variables:
    DATABASE_URL: PostgreSQL / SQLite connection string
    REMINDER_CHECK_INTERVAL_SECONDS: Polling interval (default 3600)
    REMINDER_OPEN_HOURS: Hours before 'Open' incident triggers review reminder (default 24)
    REMINDER_IN_PROGRESS_HOURS: Hours before 'Under Review' incident triggers follow-up (default 72)
"""

import os
import sys
import time
import signal
import logging

# Ensure project root is in sys.path
PORTAL_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PORTAL_ROOT not in sys.path:
    sys.path.insert(0, PORTAL_ROOT)

from backend import create_app
from backend.config.settings import (
    REMINDER_CHECK_INTERVAL_SECONDS,
    logger
)
from backend.services.notification_service import process_pending_action_reminders

_running = True


def _signal_handler(signum, frame):
    global _running
    logger.info("Shutdown signal received (%s). Stopping reminder scheduler worker...", signum)
    _running = False


def main():
    global _running

    # Register signal handlers for graceful shutdown (SIGINT, SIGTERM)
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    # Disable in-app scheduler thread since this process IS the scheduler
    app = create_app({
        "DISABLE_REMINDER_SCHEDULER": True
    })

    interval = int(os.getenv("REMINDER_CHECK_INTERVAL_SECONDS", REMINDER_CHECK_INTERVAL_SECONDS))
    logger.info("==========================================================")
    logger.info("Anti-Ragging Portal - Standalone Reminder Worker Started")
    logger.info("Check interval: %s seconds", interval)
    logger.info("Press Ctrl+C to terminate cleanly.")
    logger.info("==========================================================")

    while _running:
        try:
            with app.app_context():
                logger.info("Executing pending-action reminder cycle...")
                count = process_pending_action_reminders()
                if count > 0:
                    logger.info("Generated %d reminder notification(s).", count)
                else:
                    logger.info("No overdue incidents found. All active incidents up-to-date.")
        except Exception as e:
            logger.error("Error during reminder cycle: %s", str(e), exc_info=True)

        # Sleep in small slices to respond immediately to termination signals
        slept = 0
        while _running and slept < interval:
            time.sleep(min(1.0, interval - slept))
            slept += 1.0

    logger.info("Reminder scheduler worker stopped cleanly.")


if __name__ == "__main__":
    main()


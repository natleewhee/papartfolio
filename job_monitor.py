"""Tell the user when a scheduled job fails or is skipped, instead of only
logging it on Render.

Jobs already catch and report their own expected errors; this covers what
escapes them (an unhandled exception) and runs APScheduler skipped entirely
(a misfire). One message per failure streak: the first failure notifies,
repeats stay quiet (the 15-min alert check would otherwise spam), and the
next success sends a single "recovered" note."""
import asyncio

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED

from telegram_handler import send_telegram_message


_failing_jobs = set()


def _job_label(scheduler, job_id):
    job = scheduler.get_job(job_id)
    return job.name if job else job_id


def failure_message(event_code, label, exception=None):
    """Return the text to send for this event, or None if nothing should be sent."""
    if event_code == EVENT_JOB_ERROR:
        return f"⚠️ Scheduled job failed: {label}\n{type(exception).__name__}: {exception}"
    if event_code == EVENT_JOB_MISSED:
        return f"⚠️ Scheduled job was skipped (missed its run time): {label}"
    return None


def handle_event(event_code, job_id, label, exception=None):
    """Update the failure-streak state; return the message to send, if any."""
    if event_code == EVENT_JOB_EXECUTED:
        if job_id in _failing_jobs:
            _failing_jobs.discard(job_id)
            return f"✅ Scheduled job recovered: {label}"
        return None
    if job_id in _failing_jobs:
        return None
    _failing_jobs.add(job_id)
    return failure_message(event_code, label, exception)


def install(scheduler):
    """Attach the listener. Must be called from within the running event loop's
    thread (APScheduler's AsyncIOScheduler fires listeners there)."""

    def _listener(event):
        label = _job_label(scheduler, event.job_id)
        msg = handle_event(event.code, event.job_id, label, getattr(event, "exception", None))
        if msg:
            asyncio.get_event_loop().create_task(send_telegram_message(msg, parse_mode=None))

    scheduler.add_listener(_listener, EVENT_JOB_ERROR | EVENT_JOB_MISSED | EVENT_JOB_EXECUTED)

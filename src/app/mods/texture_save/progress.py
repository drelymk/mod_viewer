"""Best-effort progress reporting for Save to Texture."""

import logging
import time


SAVE_PROGRESS_INTERVAL = 0.15
_LOGGER = logging.getLogger(__name__)


def report_progress(callback, event):
    """Deliver best-effort domain progress without affecting the save."""
    if callback is None:
        return
    try:
        callback(dict(event))
    except Exception:
        _LOGGER.debug("Texture save progress callback failed", exc_info=True)


class SaveProgressReporter:
    """Throttle progress callbacks while keeping stage changes immediate."""

    def __init__(self, callback):
        self._callback = callback
        self._last_emit = None
        self._last_stage = None
        self._last_mip = None

    def stage(self, value):
        if value == self._last_stage:
            return
        self._last_stage = value
        self._emit({"stage": value}, force=True)

    def processing(self, mip, mip_count, completed, total):
        now = time.perf_counter()
        force = mip != self._last_mip or completed >= total
        self._last_mip = mip
        if (not force and self._last_emit is not None
                and now - self._last_emit < SAVE_PROGRESS_INTERVAL):
            return
        self._last_emit = now
        report_progress(self._callback, {
            "stage": "processing",
            "mip": mip,
            "mip_count": mip_count,
            "completed_blocks": completed,
            "total_blocks": total,
        })

    def _emit(self, event, force=False):
        now = time.perf_counter()
        if (not force and self._last_emit is not None
                and now - self._last_emit < SAVE_PROGRESS_INTERVAL):
            return
        self._last_emit = now
        report_progress(self._callback, event)

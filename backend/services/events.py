"""Persisted progress events. Every event corresponds to a real backend state transition."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from backend.database.models import AnalysisEvent

log = logging.getLogger(__name__)


class EventWriter:
    def __init__(self, session_factory, analysis_id: str):
        self.sf = session_factory
        self.analysis_id = analysis_id
        self._lock = threading.Lock()
        with self.sf() as db:
            self._seq = db.scalar(select(func.max(AnalysisEvent.seq)).where(AnalysisEvent.analysis_id == analysis_id)) or 0

    def __call__(self, stage: str, status: str, message: str, agent: str | None = None,
                 detail: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._seq += 1
            seq = self._seq
            try:
                with self.sf() as db:
                    db.add(AnalysisEvent(analysis_id=self.analysis_id, seq=seq, ts=datetime.now(UTC), stage=stage,
                                         agent=agent, status=status, message=message[:2000], detail=detail))
                    db.commit()
            except Exception:  # progress must never break the analysis
                log.exception("failed to write event")
        log.info("[%s] %s %s %s: %s", self.analysis_id[:8], stage, agent or "", status, message)


def event_dict(e: AnalysisEvent) -> dict[str, Any]:
    return {"seq": e.seq, "ts": e.ts.isoformat() if e.ts else None, "stage": e.stage, "agent": e.agent, "status": e.status,
            "message": e.message, "detail": e.detail}

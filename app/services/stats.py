from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import text
from sqlmodel import Session, func, select

from app.core.db import engine
from app.models.models import Event, Recording, Stream


def get_stats(days=7):
    """
    Returns stats per stream for the last N days.
    Structure: { stream_name: { date: { size: int, duration: float, count: int } } }
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    stats = {}
    
    with Session(engine) as session:
        query = text("""
            SELECT s.name, date(r.start_ts) as d, sum(r.size_bytes), sum(r.duration_seconds), count(r.id)
            FROM recording r
            JOIN stream s ON r.stream_id = s.id
            WHERE r.start_ts > :cutoff
            GROUP BY s.name, date(r.start_ts)
            ORDER BY d DESC
        """)
        
        results = session.exec(query, params={"cutoff": cutoff}).all()
        
        for row in results:
            name, date_str, size, duration, count = row
            if name not in stats: stats[name] = {}
            stats[name][date_str] = {
                "size_bytes": size, 
                "duration_seconds": duration, 
                "count": count
            }
            
    return stats

def _parse_sqlite_dt(value: Any) -> "datetime | None":
    """SQLite returns aggregated datetimes as strings; normalise to datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def get_asr_queue_stats(days: int = 3) -> Dict[str, Any]:
    """
    Per-station snapshot of the classification/ASR processing queue.

    A recording still needs work when it is not deleted and either has no
    classification yet, or was classified as speech but has no transcript --
    the same definition RecordingWatcher.requeue_stuck_recordings uses. The
    window (default 3 days) matches the default retention, so anything the
    cleanup job would have purged is excluded.

    Returns:
    {
      "generated_at": ISO str,
      "window_days": int,
      "totals": {total, pending_classification, pending_asr, pending_total,
                 transcribed, music, ad, errors, done_last_1h, done_last_24h,
                 throughput_per_hour, eta_hours},
      "stations": [ { per-station dict, sorted by pending_total desc }, ... ]
    }
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)
    h1 = now - timedelta(hours=1)
    h24 = now - timedelta(hours=24)

    # start_ts window lives in the JOIN so streams with no recent recordings
    # still appear (as an empty queue) rather than dropping out.
    sql = text("""
        SELECT
            s.id AS stream_id,
            s.name AS name,
            SUM(CASE WHEN r.status != 'deleted' THEN 1 ELSE 0 END) AS total,
            SUM(CASE WHEN r.status != 'deleted' AND r.classification IS NULL THEN 1 ELSE 0 END) AS pending_classification,
            SUM(CASE WHEN r.status != 'deleted' AND r.classification = 'speech' AND r.transcript IS NULL THEN 1 ELSE 0 END) AS pending_asr,
            SUM(CASE WHEN r.status != 'deleted' AND r.classification = 'speech' AND r.transcript IS NOT NULL THEN 1 ELSE 0 END) AS transcribed,
            SUM(CASE WHEN r.status != 'deleted' AND r.classification = 'music' THEN 1 ELSE 0 END) AS music,
            SUM(CASE WHEN r.status != 'deleted' AND r.classification = 'ad' THEN 1 ELSE 0 END) AS ad,
            SUM(CASE WHEN r.status = 'error' THEN 1 ELSE 0 END) AS errors,
            MIN(CASE WHEN r.status != 'deleted'
                          AND (r.classification IS NULL
                               OR (r.classification = 'speech' AND r.transcript IS NULL))
                     THEN r.start_ts END) AS oldest_pending_ts,
            SUM(CASE WHEN r.asr_ts >= :h1 THEN 1 ELSE 0 END) AS done_last_1h,
            SUM(CASE WHEN r.asr_ts >= :h24 THEN 1 ELSE 0 END) AS done_last_24h,
            AVG(CASE WHEN r.asr_ts >= :h24 THEN r.asr_processing_seconds END) AS avg_proc_seconds
        FROM stream s
        LEFT JOIN recording r ON r.stream_id = s.id AND r.start_ts >= :cutoff
        GROUP BY s.id, s.name
    """)

    stations: List[Dict[str, Any]] = []
    totals = {
        "total": 0, "pending_classification": 0, "pending_asr": 0,
        "pending_total": 0, "transcribed": 0, "music": 0, "ad": 0,
        "errors": 0, "done_last_1h": 0, "done_last_24h": 0,
    }

    with Session(engine) as session:
        rows = session.exec(sql, params={"cutoff": cutoff, "h1": h1, "h24": h24}).all()

        for row in rows:
            (stream_id, name, total, pending_classification, pending_asr,
             transcribed, music, ad, errors, oldest_pending_ts,
             done_last_1h, done_last_24h, avg_proc_seconds) = row

            total = total or 0
            pending_classification = pending_classification or 0
            pending_asr = pending_asr or 0
            pending_total = pending_classification + pending_asr

            oldest_dt = _parse_sqlite_dt(oldest_pending_ts)
            oldest_age = (now - oldest_dt).total_seconds() if oldest_dt else None

            station = {
                "stream_id": stream_id,
                "name": name,
                "total": total,
                "pending_classification": pending_classification,
                "pending_asr": pending_asr,
                "pending_total": pending_total,
                "transcribed": transcribed or 0,
                "music": music or 0,
                "ad": ad or 0,
                "errors": errors or 0,
                "oldest_pending_ts": oldest_dt.isoformat() if oldest_dt else None,
                "oldest_pending_age_seconds": oldest_age,
                "done_last_1h": done_last_1h or 0,
                "done_last_24h": done_last_24h or 0,
                "avg_proc_seconds": round(avg_proc_seconds, 1) if avg_proc_seconds else None,
            }
            stations.append(station)

            for key in ("total", "pending_classification", "pending_asr",
                        "transcribed", "music", "ad", "errors",
                        "done_last_1h", "done_last_24h"):
                totals[key] += station[key]
            totals["pending_total"] += pending_total

    stations.sort(key=lambda s: s["pending_total"], reverse=True)

    # Throughput from the last hour is the freshest signal; fall back to the
    # 24h rate if nothing finished this hour, so a quiet minute doesn't read
    # as "never drains".
    throughput_per_hour = totals["done_last_1h"] or (totals["done_last_24h"] / 24.0)
    eta_hours = (
        round(totals["pending_asr"] / throughput_per_hour, 1)
        if throughput_per_hour else None
    )
    totals["throughput_per_hour"] = round(throughput_per_hour, 1)
    totals["eta_hours"] = eta_hours

    return {
        "generated_at": now.isoformat() + "Z",
        "window_days": days,
        "totals": totals,
        "stations": stations,
    }


def get_detailed_stats(days=30) -> Dict[int, Dict[str, Any]]:
    """
    Returns detailed stats per stream.
    
    Structure:
    {
        stream_id: {
            "name": str,
            "current_status": str,
            "total_size_bytes": int, # Last N days
            "total_duration_seconds": float, # Last N days
            "today": {"size": int, "duration": float},
            "week": {"size": int, "duration": float},
            "month": {"size": int, "duration": float},
            "error_count": int, # Last N days
            "activity": [ # Last N days daily data for graph
                {"date": "YYYY-MM-DD", "hours": float, "size": int}
            ]
        }
    }
    """
    stats = {}
    
    with Session(engine) as session:
        streams = session.exec(select(Stream)).all()
        stream_map = {s.id: s.name for s in streams}
        
        for s in streams:
            stats[s.id] = {
                "name": s.name,
                "current_status": s.current_status,
                "total_size_bytes": 0,
                "total_duration_seconds": 0.0,
                "today": {"size": 0, "duration": 0.0},
                "week": {"size": 0, "duration": 0.0},
                "month": {"size": 0, "duration": 0.0},
                "error_count": 0,
                "activity": {}
            }

        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        query_recs = text("""
            SELECT r.stream_id, date(r.start_ts) as d, sum(r.size_bytes), sum(r.duration_seconds)
            FROM recording r
            WHERE r.start_ts >= :cutoff
            GROUP BY r.stream_id, date(r.start_ts)
        """)
        
        results_recs = session.exec(query_recs, params={"cutoff": cutoff_date}).all()
        
        now = datetime.utcnow()
        today_str = now.strftime("%Y-%m-%d")
        week_cutoff = now - timedelta(days=7)
        month_cutoff = now - timedelta(days=30)
        
        for row in results_recs:
            sid, date_str, size, duration = row
            if sid not in stats: continue
            
            size = size or 0
            duration = duration or 0.0
            
            stats[sid]["total_size_bytes"] += size
            stats[sid]["total_duration_seconds"] += duration
            
            stats[sid]["activity"][date_str] = {
                "date": date_str,
                "hours": round(duration / 3600.0, 2),
                "size_mb": round(size / (1024*1024), 2)
            }
            
            row_date = datetime.strptime(date_str, "%Y-%m-%d")
            
            if date_str == today_str:
                stats[sid]["today"]["size"] += size
                stats[sid]["today"]["duration"] += duration
                
            if row_date >= week_cutoff:
                stats[sid]["week"]["size"] += size
                stats[sid]["week"]["duration"] += duration
                
            if row_date >= month_cutoff:
                stats[sid]["month"]["size"] += size
                stats[sid]["month"]["duration"] += duration

        query_errs = text("""
            SELECT stream_id, count(*)
            FROM event
            WHERE level = 'error' AND ts >= :cutoff
            GROUP BY stream_id
        """)
        results_errs = session.exec(query_errs, params={"cutoff": cutoff_date}).all()
        
        for row in results_errs:
            sid, count = row
            if sid in stats:
                stats[sid]["error_count"] = count

        date_range = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
        date_range.reverse()
        
        for sid in stats:
            activity_list = []
            for d in date_range:
                if d in stats[sid]["activity"]:
                    activity_list.append(stats[sid]["activity"][d])
                else:
                    activity_list.append({"date": d, "hours": 0.0, "size_mb": 0.0})
            stats[sid]["activity"] = activity_list

    return stats

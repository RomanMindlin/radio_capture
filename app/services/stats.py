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

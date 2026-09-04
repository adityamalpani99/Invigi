#!/usr/bin/env python3
"""
Invigi — real-time study accountability assistant (local server).

Zero dependencies: Python 3.8+ standard library only.

Run:      python steady.py
Options:  python steady.py --port 9000     (or env STEADY_PORT)

What this server does:
  * Serves the app from steady.html (must sit in the same folder).
  * Persists sessions + settings in steady.db (SQLite, same folder).
  * Computes daily / weekly / trend analytics in Python from stored rows.
  * Provides JSON export and data reset.

What it never does: it never sees camera frames or audio — all sensing
stays inside the browser, on the student's device.
"""

import json
import os
import socket
import sqlite3
import sys
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "steady.db")
HTML_PATH = os.path.join(ROOT, "steady.html")
BASE_PORT = int(os.environ.get("STEADY_PORT", "8757"))
if "--port" in sys.argv:
    try:
        BASE_PORT = int(sys.argv[sys.argv.index("--port") + 1])
    except (ValueError, IndexError):
        pass

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  day TEXT,
  start_clock TEXT, end_clock TEXT,
  subject TEXT DEFAULT '', task TEXT DEFAULT '', target TEXT DEFAULT '',
  goal_minutes REAL DEFAULT 0,
  focused_ms INTEGER DEFAULT 0, possibly_ms INTEGER DEFAULT 0,
  distracted_ms INTEGER DEFAULT 0, break_ms INTEGER DEFAULT 0,
  paused_ms INTEGER DEFAULT 0, study_ms INTEGER DEFAULT 0,
  focus_score INTEGER, longest_distraction_ms INTEGER DEFAULT 0,
  goal_met INTEGER DEFAULT 0, mode TEXT DEFAULT 'focus',
  monitoring TEXT DEFAULT 'timer-only', corrections INTEGER DEFAULT 0,
  events TEXT DEFAULT '[]', segments TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day);
"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript(SCHEMA)


# ----------------------------------------------------------------- helpers
def parse_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def age_days(iso):
    dt = parse_iso(iso)
    if not dt:
        return 1e9
    return (datetime.utcnow() - dt).total_seconds() / 86400.0


def epoch_ms(iso):
    dt = parse_iso(iso)
    return dt.timestamp() * 1000 if dt else 0


def lsq_slope(ys):
    """Least-squares slope — real trend math, no guessing."""
    n = len(ys)
    if n < 2:
        return None
    xs = list(range(n))
    sx, sy = sum(xs), sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sx2 = sum(x * x for x in xs)
    den = n * sx2 - sx * sx
    return (n * sxy - sx * sy) / den if den else 0.0


# ------------------------------------------------------------ persistence
def save_session(rec):
    rec = rec or {}
    d = rec.get("durations") or {}

    def gi(k, default=0):
        try:
            return int(float(d.get(k, default) or 0))
        except (TypeError, ValueError):
            return default

    focused = gi("focusedMs")
    possibly = gi("possiblyMs")
    distracted = gi("distractedMs")
    break_ms = gi("breakMs")
    paused_ms = gi("pausedMs")
    study = focused + possibly + distracted          # server-side integrity recompute
    score = None
    if study >= 30000:                                # same honesty rule as the client
        score = int(round((focused + 0.5 * possibly) / study * 100))
    try:
        goal_min = float(rec.get("goalMinutes") or 0)
    except (TypeError, ValueError):
        goal_min = 0.0
    goal_met = 1 if (rec.get("goalMet") or (goal_min > 0 and study >= goal_min * 60000)) else 0
    sid = str(rec.get("id") or ("s" + datetime.utcnow().strftime("%Y%m%d%H%M%S%f")))
    date = str(rec.get("date") or datetime.utcnow().isoformat() + "Z")
    day = str(rec.get("day") or date[:10])

    with db() as c:
        c.execute(
            """INSERT OR REPLACE INTO sessions
               (id,date,day,start_clock,end_clock,subject,task,target,goal_minutes,
                focused_ms,possibly_ms,distracted_ms,break_ms,paused_ms,study_ms,
                focus_score,longest_distraction_ms,goal_met,mode,monitoring,
                corrections,events,segments)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, date, day,
             str(rec.get("startClock") or ""), str(rec.get("endClock") or ""),
             str(rec.get("subject") or ""), str(rec.get("task") or ""),
             str(rec.get("target") or ""), goal_min,
             focused, possibly, distracted, break_ms, paused_ms, study,
             score, int(rec.get("longestDistractionMs") or 0), goal_met,
             str(rec.get("mode") or "focus"), str(rec.get("monitoring") or "timer-only"),
             int(rec.get("corrections") or 0),
             json.dumps(rec.get("events") or []),
             json.dumps(rec.get("segments") or [])))
    return {"ok": True, "id": sid, "study_ms": study, "focus_score": score}


def delete_session(sid):
    with db() as c:
        c.execute("DELETE FROM sessions WHERE id=?", (str(sid),))
    return {"ok": True}


def list_sessions():
    out = []
    with db() as c:
        rows = c.execute("SELECT * FROM sessions ORDER BY date DESC").fetchall()
    for r in rows:
        out.append({
            "id": r["id"], "date": r["date"], "day": r["day"],
            "startClock": r["start_clock"], "endClock": r["end_clock"],
            "subject": r["subject"], "task": r["task"], "target": r["target"],
            "goalMinutes": r["goal_minutes"],
            "durations": {"focusedMs": r["focused_ms"], "possiblyMs": r["possibly_ms"],
                          "distractedMs": r["distracted_ms"], "breakMs": r["break_ms"],
                          "pausedMs": r["paused_ms"]},
            "studyMs": r["study_ms"], "focusScore": r["focus_score"],
            "longestDistractionMs": r["longest_distraction_ms"],
            "goalMet": bool(r["goal_met"]), "mode": r["mode"],
            "monitoring": r["monitoring"], "corrections": r["corrections"],
            "events": json.loads(r["events"] or "[]"),
            "segments": json.loads(r["segments"] or "[]"),
        })
    return out


def get_settings():
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='app'").fetchone()
    return json.loads(row["value"]) if row else None


def put_settings(blob):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('app',?)",
                  (json.dumps(blob or {}),))
    return {"ok": True}


def reset_all():
    with db() as c:
        c.execute("DELETE FROM sessions")
        c.execute("DELETE FROM settings")
    return {"ok": True}


# -------------------------------------------------------------- analytics
def analytics():
    rows = []
    with db() as c:
        for r in c.execute("SELECT * FROM sessions ORDER BY date ASC"):
            rows.append({
                "date": r["date"], "day": r["day"],
                "study_ms": r["study_ms"] or 0,
                "focused_ms": (r["focused_ms"] or 0), "possibly_ms": r["possibly_ms"] or 0,
                "distracted_ms": r["distracted_ms"] or 0,
                "focus_score": r["focus_score"],
            })
    focused_of = lambda r: r["focused_ms"] + r["possibly_ms"]

    today = datetime.now().date()
    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"] or r["date"][:10], []).append(r)

    daily = []
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        day_rows = by_day.get(d.isoformat(), [])
        scores = [r["focus_score"] for r in day_rows if r["focus_score"] is not None]
        daily.append({
            "date": d.isoformat(), "label": str(d.day),
            "study_ms": sum(r["study_ms"] for r in day_rows),
            "focused_ms": sum(focused_of(r) for r in day_rows),
            "distracted_ms": sum(r["distracted_ms"] for r in day_rows),
            "sessions": len(day_rows),
            "avg_score": round(sum(scores) / len(scores)) if scores else None,
        })

    week = [r for r in rows if age_days(r["date"]) <= 7]
    wk_scores = [r["focus_score"] for r in week if r["focus_score"] is not None]
    weekly = {
        "study_hours": sum(r["study_ms"] for r in week) / 3.6e6,
        "sessions": len(week),
        "avg_score": round(sum(wk_scores) / len(wk_scores)) if wk_scores else None,
        "longest_ms": max((r["study_ms"] for r in week), default=0),
    }

    per_weekday = {}
    for r in rows:
        if age_days(r["date"]) > 28:
            continue
        try:
            wd = WEEKDAYS[datetime.strptime(r["day"] or r["date"][:10], "%Y-%m-%d").weekday()]
        except ValueError:
            continue
        per_weekday[wd] = per_weekday.get(wd, 0) + focused_of(r)
    productive = None
    if per_weekday:
        wd, ms = max(per_weekday.items(), key=lambda kv: kv[1])
        productive = {"day": wd, "focused_ms": ms}

    scored = [(r["date"], r["focus_score"]) for r in rows
              if r["focus_score"] is not None][-12:]
    trend = {"slope": lsq_slope([s for _, s in scored]), "n": len(scored),
             "points": [{"x": epoch_ms(d), "y": s} for d, s in scored]}
    return {"daily": daily, "weekly": weekly,
            "productive_day": productive, "trend": trend}


# ---------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    server_version = "Invigi/1.0"

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200, extra=None):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj).encode(), extra)

    def _html(self):
        if not os.path.exists(HTML_PATH):
            msg = ("steady.html was not found next to steady.py.\n"
                   "Looked in: " + ROOT).encode()
            return self._send(500, "text/plain; charset=utf-8", msg)
        with open(HTML_PATH, "rb") as f:
            self._send(200, "text/html; charset=utf-8", f.read())

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html", "/steady.html"):
            return self._html()
        if p == "/health":
            return self._json({"ok": True, "app": "steady",
                               "time": datetime.now().isoformat()})
        if p == "/api/sessions":
            return self._json({"sessions": list_sessions()})
        if p == "/api/analytics":
            return self._json(analytics())
        if p == "/api/settings":
            return self._json({"settings": get_settings()})
        if p == "/api/export":
            payload = json.dumps({
                "exportedAt": datetime.now().isoformat(),
                "sessions": list_sessions(), "settings": get_settings(),
            }, indent=2).encode()
            return self._send(200, "application/json", payload,
                              {"Content-Disposition":
                               'attachment; filename="steady-data.json"'})
        if p == "/favicon.ico":
            return self._send(204, "text/plain", b"")
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json({"error": "bad json body"}, 400)
        if p == "/api/sessions":
            return self._json(save_session(body))
        if p == "/api/sessions/delete":
            return self._json(delete_session(body.get("id", "")))
        if p == "/api/settings":
            return self._json(put_settings(body.get("settings")))
        if p == "/api/reset":
            return self._json(reset_all())
        self._json({"error": "not found"}, 404)

    def log_message(self, *args):  # keep the console calm
        pass


def main():
    if not os.path.exists(HTML_PATH):
        print("ERROR: steady.html was not found next to steady.py")
        print("Expected at: " + HTML_PATH)
        sys.exit(1)
    init_db()

    port = BASE_PORT
    for cand in range(BASE_PORT, BASE_PORT + 30):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", cand))
                port = cand
                break
            except OSError:
                continue

    url = "http://127.0.0.1:%d" % port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    line = "-" * 60
    print("\n" + line)
    print("  Invigi — study accountability")
    print("  Open:   %s   (a browser window will open)" % url)
    print("  Data:   %s" % DB_PATH)
    print("  Stop:   press Ctrl+C")
    print(line + "\n")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nInvigi closed. Your sessions remain saved in steady.db")


if __name__ == "__main__":
    main()
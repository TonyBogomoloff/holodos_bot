import json
import re
import secrets
import sqlite3
import time
from pathlib import Path


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
              owner INTEGER PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
              id INTEGER PRIMARY KEY AUTOINCREMENT, update_id INTEGER UNIQUE NOT NULL,
              owner INTEGER NOT NULL, payload TEXT NOT NULL, cache TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
              available REAL NOT NULL DEFAULT 0, created REAL NOT NULL, error TEXT);
            CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status,available,id);
            CREATE TABLE IF NOT EXISTS effects(
              key TEXT PRIMARY KEY, status TEXT NOT NULL, result TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS quota(
              day INTEGER NOT NULL, owner INTEGER NOT NULL, used INTEGER NOT NULL,
              PRIMARY KEY(day,owner));
        """)

    def close(self):
        self.db.close()

    def recover(self):
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending' WHERE status='running'")
            self.db.execute("UPDATE effects SET status='unknown' WHERE status='sending'")

    def session(self, owner):
        row = self.db.execute("SELECT state FROM sessions WHERE owner=?", (owner,)).fetchone()
        return json.loads(row[0]) if row else {
            "rev": secrets.randbits(40), "inventory": [], "previous": None, "photos": [], "recipes": [],
            "panel": None, "preferences": "", "searched": False,
        }

    def save(self, owner, state):
        self.db.execute("INSERT INTO sessions VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE "
                        "SET state=excluded.state,updated=excluded.updated", (owner, encoded(state), time.time()))

    def offset(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
        return row[0] if row else 0

    def ingest(self, update_id, owner, payload):
        accepted = True
        with self.db:
            if owner and payload:
                # Callback IDs differ on every tap. Coalesce the same dish while its
                # durable job is queued, running, or waiting for a retry.
                duplicate_pick = (
                    payload.get('kind') == 'callback'
                    and re.fullmatch(r'h:\d+:pick:\d+', payload.get('data', ''))
                    and self.db.execute(
                        "SELECT 1 FROM jobs WHERE owner=? AND status IN ('pending','running') "
                        "AND json_extract(payload,'$.kind')='callback' "
                        "AND json_extract(payload,'$.data')=? LIMIT 1",
                        (owner, payload['data'])).fetchone()
                )
                pending = self.db.execute("SELECT COUNT(*) FROM jobs WHERE owner=? AND status IN "
                                          "('pending','running')", (owner,)).fetchone()[0]
                total = self.db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('pending','running')").fetchone()[0]
                # Bound incoming work without acknowledging and discarding accepted jobs later.
                if duplicate_pick:
                    pass  # Acknowledge the update and advance the offset without another job.
                elif pending < 25 and total < 500:
                    self.db.execute("INSERT OR IGNORE INTO jobs(update_id,owner,payload,created) VALUES(?,?,?,?)",
                                    (update_id, owner, encoded(payload), time.time()))
                elif not self.db.execute("SELECT 1 FROM jobs WHERE update_id=?", (update_id,)).fetchone():
                    accepted = False
            self.db.execute("INSERT INTO meta VALUES('offset',?) ON CONFLICT(key) DO UPDATE "
                            "SET value=MAX(value,excluded.value)", (update_id + 1,))
        return accepted

    def claim(self):
        with self.db:
            row = self.db.execute("""SELECT * FROM jobs j WHERE status='pending' AND available<=?
                AND NOT EXISTS(SELECT 1 FROM jobs p WHERE p.owner=j.owner AND
                (p.status='running' OR (p.status='pending' AND p.id<j.id))) ORDER BY id LIMIT 1""",
                                  (time.time(),)).fetchone()
            if not row:
                return None
            self.db.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=?", (row["id"],))
        job = dict(row)
        job["payload"], job["cache"] = json.loads(job["payload"]), json.loads(job["cache"])
        job["attempts"] += 1
        return job

    def checkpoint(self, job, key, value, state=None):
        job["cache"][key] = value
        with self.db:
            if state is not None:
                self.save(job["owner"], state)
            self.db.execute("UPDATE jobs SET cache=? WHERE id=?", (encoded(job["cache"]), job["id"]))

    def finish(self, job, error=None, retry=0):
        with self.db:
            self.db.execute("UPDATE jobs SET status=?,available=?,error=? WHERE id=?",
                            ("pending" if retry else "failed" if error else "done",
                             time.time() + retry, error, job["id"]))

    def reserve(self, job, per_user, total):
        if job["cache"].get("quota"):
            return True
        day = int(time.time() // 86400)
        with self.db:
            for owner, limit in [(0, total), (job["owner"], per_user)]:
                row = self.db.execute("SELECT used FROM quota WHERE day=? AND owner=?", (day, owner)).fetchone()
                if row and row[0] >= limit:
                    return False
            for owner in [0, job["owner"]]:
                self.db.execute("INSERT INTO quota VALUES(?,?,1) ON CONFLICT(day,owner) DO UPDATE SET used=used+1",
                                (day, owner))
            job["cache"]["quota"] = True
            self.db.execute("UPDATE jobs SET cache=? WHERE id=?", (encoded(job["cache"]), job["id"]))
        return True

    def effect(self, key):
        row = self.db.execute("SELECT * FROM effects WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def effect_set(self, key, status, result=None):
        with self.db:
            self.db.execute("INSERT INTO effects VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE "
                            "SET status=excluded.status,result=excluded.result",
                            (key, status, encoded(result), time.time()))

    def cleanup(self, hours):
        cutoff = time.time() - hours * 3600
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE updated<? AND owner NOT IN "
                            "(SELECT owner FROM jobs WHERE status IN ('pending','running'))", (cutoff,))
            self.db.execute("DELETE FROM jobs WHERE created<? AND status IN ('done','failed')", (cutoff,))
            self.db.execute("DELETE FROM effects WHERE created<?", (cutoff,))
            self.db.execute("DELETE FROM quota WHERE day<?", (int(time.time() // 86400) - 2,))
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def stats(self):
        return {r[0]: r[1] for r in self.db.execute("SELECT status,count(*) FROM jobs GROUP BY status")}

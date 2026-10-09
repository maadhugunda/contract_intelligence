"""SQLite-backed task leasing and fenced result publication (capstone v0.1)."""
import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Lease:
    task_id: str
    owner: str
    generation: int


class TaskRegistry:
    def __init__(self, path: str):
        self.path = path
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'PENDING',
                owner TEXT, generation INTEGER NOT NULL DEFAULT 0,
                expires_at REAL, artifact_uri TEXT, artifact_sha256 TEXT)''')

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def create_task(self, task_id: str):
        with self._db() as db:
            db.execute('INSERT OR IGNORE INTO tasks(task_id) VALUES (?)', (task_id,))

    def acquire_lease(self, task_id: str, owner: str, ttl: float = 60, now=None):
        if ttl <= 0:
            raise ValueError('ttl must be positive')
        now = time.time() if now is None else now
        with self._db() as db:
            row = db.execute('''UPDATE tasks SET owner=?, generation=generation+1,
                expires_at=?, status='PROCESSING'
                WHERE task_id=? AND (status='PENDING' OR
                  (status='PROCESSING' AND expires_at <= ?))
                RETURNING generation''', (owner, now+ttl, task_id, now)).fetchone()
            return Lease(task_id, owner, row['generation']) if row else None

    def renew_lease(self, lease: Lease, ttl: float = 60, now=None):
        if ttl <= 0:
            raise ValueError('ttl must be positive')
        now = time.time() if now is None else now
        with self._db() as db:
            result = db.execute('''UPDATE tasks SET expires_at=?
                WHERE task_id=? AND owner=? AND generation=?
                AND status='PROCESSING' AND expires_at > ?''',
                (now+ttl, lease.task_id, lease.owner, lease.generation, now))
            return result.rowcount == 1

    def commit_result(self, lease: Lease, artifact_uri: str, sha256: str, now=None):
        """Artifact is already immutable and verified by caller; this fences publication."""
        now = time.time() if now is None else now
        if not artifact_uri or len(sha256) != 64 or any(c not in '0123456789abcdef' for c in sha256):
            raise ValueError('artifact URI and lowercase SHA-256 are required')
        with self._db() as db:
            result = db.execute('''UPDATE tasks SET status='COMPLETED',
                artifact_uri=?, artifact_sha256=?
                WHERE task_id=? AND owner=? AND generation=?
                AND status='PROCESSING' AND expires_at > ?''',
                (artifact_uri, sha256, lease.task_id, lease.owner, lease.generation, now))
            return result.rowcount == 1

    def get_task(self, task_id: str):
        with self._db() as db:
            row = db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            return dict(row) if row else None

"""SQLite task leasing, fenced publication, idempotent acknowledgments, and outbox."""
from __future__ import annotations
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Lease:
    task_id: str
    owner: str
    generation: int


class CommitStatus(str, Enum):
    COMMITTED = 'COMMITTED'
    ALREADY_COMMITTED = 'ALREADY_COMMITTED'
    CONFLICT = 'CONFLICT'
    STALE_LEASE = 'STALE_LEASE'


@dataclass(frozen=True)
class CommitOutcome:
    status: CommitStatus
    state_changed: bool
    accepted_generation: int | None = None


class TaskRegistry:
    def __init__(self, path: str):
        self.path = path
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'PENDING',
                owner TEXT, generation INTEGER NOT NULL DEFAULT 0,
                expires_at REAL, artifact_uri TEXT, artifact_sha256 TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                event_type TEXT NOT NULL, created_at REAL NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id))''')

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
        """Acknowledges exact accepted retries, but never grants a stale worker a new write.

        Caller must have verified the immutable artifact before calling.
        """
        now = time.time() if now is None else now
        if not artifact_uri or len(sha256) != 64 or any(c not in '0123456789abcdef' for c in sha256):
            raise ValueError('artifact URI and lowercase SHA-256 are required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tasks WHERE task_id=?', (lease.task_id,)).fetchone()
            if row is None:
                return CommitOutcome(CommitStatus.STALE_LEASE, False)
            if row['status'] == 'COMPLETED':
                same_attempt = row['owner'] == lease.owner and row['generation'] == lease.generation
                same_artifact = row['artifact_uri'] == artifact_uri and row['artifact_sha256'] == sha256
                if same_attempt and same_artifact:
                    return CommitOutcome(CommitStatus.ALREADY_COMMITTED, False, row['generation'])
                return CommitOutcome(CommitStatus.CONFLICT, False, row['generation'])
            if (row['status'] != 'PROCESSING' or row['owner'] != lease.owner or
                    row['generation'] != lease.generation or row['expires_at'] <= now):
                return CommitOutcome(CommitStatus.STALE_LEASE, False)
            db.execute('''UPDATE tasks SET status='COMPLETED', artifact_uri=?, artifact_sha256=?
                WHERE task_id=?''', (artifact_uri, sha256, lease.task_id))
            db.execute('''INSERT INTO outbox(event_id, task_id, event_type, created_at)
                VALUES (?, ?, 'TaskCompleted', ?)''', (f'{lease.task_id}:completed', lease.task_id, now))
            return CommitOutcome(CommitStatus.COMMITTED, True, lease.generation)

    def get_task(self, task_id: str):
        with self._db() as db:
            row = db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            return dict(row) if row else None

    def get_outbox_events(self):
        with self._db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM outbox ORDER BY event_id')]

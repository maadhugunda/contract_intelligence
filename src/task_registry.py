"""SQLite task leasing, fenced publication, idempotent acknowledgments, and outbox."""
from __future__ import annotations
import sqlite3
import json
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
            db.execute('''CREATE TABLE IF NOT EXISTS extraction_runs (
                extraction_run_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                source_version_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                pipeline_fingerprint TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS extraction_run_contracts (
                extraction_run_id TEXT PRIMARY KEY, source_format TEXT NOT NULL,
                pipeline_manifest TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS extraction_tasks (
                task_id TEXT PRIMARY KEY, extraction_run_id TEXT NOT NULL,
                stage TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS source_revisions (
                document_id TEXT NOT NULL, source_version_id TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, PRIMARY KEY(document_id, source_version_id))''')
            # Backfill old databases only when their history is unambiguous.
            db.execute('''INSERT OR IGNORE INTO source_revisions
                SELECT document_id, source_version_id, MIN(source_sha256) FROM extraction_runs
                GROUP BY document_id, source_version_id HAVING COUNT(DISTINCT source_sha256)=1''')

    def bind_extraction_run(self, extraction_run_id, document_id, source_version_id,
                            source_sha256, pipeline_fingerprint, *, pipeline_manifest=None,
                            source_format=None):
        """Atomically register an immutable binding, or acknowledge an exact retry."""
        values = (extraction_run_id, document_id, source_version_id,
                  source_sha256, pipeline_fingerprint)
        if any(not value or not value.strip() for value in values):
            raise ValueError('Extraction run binding fields must be non-empty')
        for digest in (source_sha256, pipeline_fingerprint):
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('Run fingerprints must be lowercase SHA-256 values')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            legacy = db.execute('''SELECT COUNT(DISTINCT source_sha256) FROM extraction_runs
                WHERE document_id=? AND source_version_id=?''', (document_id, source_version_id)).fetchone()[0]
            if legacy > 1:
                raise ValueError('Source revision has conflicting legacy hashes; resolve its history first')
            db.execute('INSERT OR IGNORE INTO extraction_runs VALUES (?, ?, ?, ?, ?)', values)
            row = db.execute('SELECT * FROM extraction_runs WHERE extraction_run_id=?',
                             (extraction_run_id,)).fetchone()
            if tuple(row) != values:
                names = ('extraction_run_id', 'document_id', 'source_version_id',
                         'source_sha256', 'pipeline_fingerprint')
                changes = [f'{name}: registered={old!r}, submitted={new!r}'
                           for name, old, new in zip(names, tuple(row), values) if old != new]
                raise ValueError(
                    f'Extraction run {extraction_run_id!r} is already bound to a different source or pipeline.\n'
                    + '\n'.join(changes)
                    + '\nFor a retry, use the original source, revision and pipeline. '
                    'For a new extraction, omit --extraction-run-id during planning to generate a new run. '
                    'If the file bytes changed, also provide a new --source-version-id.')
            db.execute('INSERT OR IGNORE INTO source_revisions VALUES (?, ?, ?)',
                       (document_id, source_version_id, source_sha256))
            revision = db.execute('SELECT source_sha256 FROM source_revisions WHERE document_id=? AND source_version_id=?',
                                  (document_id, source_version_id)).fetchone()
            if revision[0] != source_sha256:
                raise ValueError(
                    f'Source revision conflict for document {document_id!r}, revision {source_version_id!r}.\n'
                    f'Registered SHA-256: {revision[0]}\n'
                    f'Submitted SHA-256:  {source_sha256}\n'
                    'The submitted file contains different bytes from the registered revision. '
                    'Submit the updated file with a new --source-version-id and a new extraction run '
                    '(omit --extraction-run-id during planning to generate one). '
                    'To retry the existing revision, use the original file. '
                    'Registration was rejected; the stored revision was not changed.')
            if pipeline_manifest is not None:
                if not source_format:
                    raise ValueError('Planned run requires source format')
                serialized = json.dumps(pipeline_manifest, sort_keys=True, separators=(',', ':'), allow_nan=False)
                import hashlib
                if hashlib.sha256(serialized.encode()).hexdigest() != pipeline_fingerprint:
                    raise ValueError('Pipeline manifest fingerprint mismatch')
                db.execute('INSERT OR IGNORE INTO extraction_run_contracts VALUES (?, ?, ?)',
                           (extraction_run_id, source_format, serialized))
                contract = db.execute('SELECT source_format, pipeline_manifest FROM extraction_run_contracts WHERE extraction_run_id=?',
                                      (extraction_run_id,)).fetchone()
                if tuple(contract) != (source_format, serialized):
                    raise ValueError('Extraction run contract is immutable')

    def get_run_contract(self, extraction_run_id):
        with self._db() as db:
            row = db.execute('''SELECT r.*, c.source_format, c.pipeline_manifest
                FROM extraction_runs r JOIN extraction_run_contracts c USING (extraction_run_id)
                WHERE extraction_run_id=?''', (extraction_run_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            hashes = db.execute('SELECT DISTINCT source_sha256 FROM extraction_runs WHERE document_id=? AND source_version_id=?',
                                (result['document_id'], result['source_version_id'])).fetchall()
            if len(hashes) > 1:
                raise ValueError('Source revision has conflicting legacy hashes; resolve its history first')
            result['pipeline_manifest'] = json.loads(result['pipeline_manifest'])
            return result

    def create_extraction_task(self, task_id, extraction_run_id, stage='native_extraction'):
        """Associate a leased task with a preplanned run/stage, immutably."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT pipeline_manifest FROM extraction_run_contracts WHERE extraction_run_id=?',
                             (extraction_run_id,)).fetchone()
            if row is None or stage not in json.loads(row[0])['stages']:
                raise ValueError('Task requires a planned run and known pipeline stage')
            db.execute('INSERT OR IGNORE INTO tasks(task_id) VALUES (?)', (task_id,))
            db.execute('INSERT OR IGNORE INTO extraction_tasks VALUES (?, ?, ?)',
                       (task_id, extraction_run_id, stage))
            bound = db.execute('SELECT extraction_run_id, stage FROM extraction_tasks WHERE task_id=?', (task_id,)).fetchone()
            if tuple(bound) != (extraction_run_id, stage):
                raise ValueError('Extraction task binding is immutable')

    def get_extraction_run(self, extraction_run_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM extraction_runs WHERE extraction_run_id=?',
                             (extraction_run_id,)).fetchone()
            return dict(row) if row else None

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

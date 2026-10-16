import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from task_registry import TaskRegistry, CommitStatus

DIGEST = hashlib.sha256(b'immutable output').hexdigest()
OTHER = hashlib.sha256(b'different output').hexdigest()

class RegistryTests(unittest.TestCase):
    def test_revision_binding_across_runs(self):
        self.registry.bind_extraction_run('a', 'D', 'V', DIGEST, DIGEST)
        self.registry.bind_extraction_run('b', 'D', 'V', DIGEST, OTHER)
        with self.assertRaisesRegex(ValueError, 'Source revision') as conflict:
            self.registry.bind_extraction_run('c', 'D', 'V', OTHER, DIGEST)
        message = str(conflict.exception)
        for expected in ("document 'D'", "revision 'V'", DIGEST, OTHER,
                         '--source-version-id', 'stored revision was not changed'):
            self.assertIn(expected, message)
        self.assertIsNone(self.registry.get_extraction_run('c'))
        self.registry.bind_extraction_run('d', 'D', 'V2', OTHER, DIGEST)
        restarted = TaskRegistry(self.registry.path)
        with self.assertRaisesRegex(ValueError, 'Source revision'):
            restarted.bind_extraction_run('e', 'D', 'V', OTHER, DIGEST)

    def test_concurrent_run_binding_only_accepts_one_source(self):
        def bind(digest):
            try:
                self.registry.bind_extraction_run('run', 'D', 'V', digest, DIGEST)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(bind, [DIGEST, OTHER]))
        self.assertEqual(sorted(results), [False, True])
        accepted = self.registry.get_extraction_run('run')['source_sha256']
        self.registry.bind_extraction_run('run', 'D', 'V', accepted, DIGEST)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = TaskRegistry(str(Path(self.tmp.name) / 'tasks.db'))
        self.registry.create_task('T3')

    def test_stale_worker_cannot_commit(self):
        a = self.registry.acquire_lease('T3', 'A', ttl=10, now=100)
        self.assertIsNone(self.registry.acquire_lease('T3', 'B', ttl=10, now=105))
        b = self.registry.acquire_lease('T3', 'B', ttl=10, now=110)
        self.assertEqual((a.generation, b.generation), (1, 2))
        self.assertEqual(self.registry.commit_result(a, 'a.json', DIGEST, now=111).status, CommitStatus.STALE_LEASE)
        self.assertEqual(self.registry.commit_result(b, 'b.json', DIGEST, now=111).status, CommitStatus.COMMITTED)
        self.assertEqual(self.registry.get_task('T3')['artifact_uri'], 'b.json')

    def test_renewal_prevents_takeover(self):
        a = self.registry.acquire_lease('T3', 'A', ttl=10, now=100)
        self.assertTrue(self.registry.renew_lease(a, ttl=10, now=105))
        self.assertIsNone(self.registry.acquire_lease('T3', 'B', now=111))
        self.assertEqual(self.registry.commit_result(a, 'a.json', DIGEST, now=111).status, CommitStatus.COMMITTED)
        self.assertIsNone(self.registry.acquire_lease('T3', 'B', now=120))

    def test_expired_lease_cannot_renew_or_commit(self):
        a = self.registry.acquire_lease('T3', 'A', ttl=10, now=100)
        self.assertFalse(self.registry.renew_lease(a, now=110))
        self.assertEqual(self.registry.commit_result(a, 'a.json', DIGEST, now=110).status, CommitStatus.STALE_LEASE)

    def test_idempotent_retry_after_expiry_one_outbox_event(self):
        self.registry.create_task('T3')
        a = self.registry.acquire_lease('T3', 'A', ttl=10, now=100)
        first = self.registry.commit_result(a, 'a.json', DIGEST, now=101)
        retry = self.registry.commit_result(a, 'a.json', DIGEST, now=999)
        self.assertEqual(first.status, CommitStatus.COMMITTED)
        self.assertTrue(first.state_changed)
        self.assertEqual(retry.status, CommitStatus.ALREADY_COMMITTED)
        self.assertFalse(retry.state_changed)
        self.assertEqual(len(self.registry.get_outbox_events()), 1)

    def test_different_artifact_conflicts(self):
        a = self.registry.acquire_lease('T3', 'A', now=100)
        self.registry.commit_result(a, 'a.json', DIGEST, now=101)
        self.assertEqual(self.registry.commit_result(a, 'other.json', OTHER, now=102).status, CommitStatus.CONFLICT)
        self.assertEqual(self.registry.get_task('T3')['artifact_uri'], 'a.json')
        self.assertEqual(len(self.registry.get_outbox_events()), 1)

    def test_stale_attempt_not_accepted_even_same_hash(self):
        a = self.registry.acquire_lease('T3', 'A', ttl=10, now=100)
        b = self.registry.acquire_lease('T3', 'B', ttl=10, now=110)
        self.registry.commit_result(b, 'same.json', DIGEST, now=111)
        self.assertEqual(self.registry.commit_result(a, 'same.json', DIGEST, now=112).status, CommitStatus.CONFLICT)

if __name__ == '__main__':
    unittest.main()

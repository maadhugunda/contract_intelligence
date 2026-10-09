import hashlib
import tempfile
import unittest
from pathlib import Path
from task_registry import TaskRegistry

DIGEST = hashlib.sha256(b'immutable output').hexdigest()

class RegistryTests(unittest.TestCase):
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
        self.assertFalse(self.registry.commit_result(a, 'a.json', DIGEST, now=111))
        self.assertTrue(self.registry.commit_result(b, 'b.json', DIGEST, now=111))
        self.assertEqual(self.registry.get_task('T3')['artifact_uri'], 'b.json')

    def test_renewal_prevents_takeover(self):
        a = self.registry.acquire_lease('T3', 'A', ttl=10, now=100)
        self.assertTrue(self.registry.renew_lease(a, ttl=10, now=105))
        self.assertIsNone(self.registry.acquire_lease('T3', 'B', now=111))
        self.assertTrue(self.registry.commit_result(a, 'a.json', DIGEST, now=111))
        self.assertIsNone(self.registry.acquire_lease('T3', 'B', now=120))

    def test_expired_lease_cannot_renew_or_commit(self):
        a = self.registry.acquire_lease('T3', 'A', ttl=10, now=100)
        self.assertFalse(self.registry.renew_lease(a, now=110))
        self.assertFalse(self.registry.commit_result(a, 'a.json', DIGEST, now=110))

    def test_create_is_idempotent_and_commit_once(self):
        self.registry.create_task('T3')
        a = self.registry.acquire_lease('T3', 'A', now=100)
        self.assertEqual(a.generation, 1)
        self.assertTrue(self.registry.commit_result(a, 'a.json', DIGEST, now=101))
        self.assertFalse(self.registry.commit_result(a, 'other.json', DIGEST, now=102))
        self.assertEqual(self.registry.get_task('T3')['artifact_uri'], 'a.json')

if __name__ == '__main__':
    unittest.main()

"""Connection budget must hold across institutions, not only within each pool."""
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from backend import db


class FakeConnection:
    def __init__(self, name):
        self.database = name
        self.closed = False
        self.readonly = False
    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql): self.readonly = False
    def rollback(self): pass
    def close(self): self.closed = True
    def ping(self, **kwargs):
        if self.closed: raise db.pymysql.OperationalError('closed')


class BusinessPoolBudget(unittest.TestCase):
    def setUp(self):
        self.old_idle, self.old_total = db._POOL_IDLE, db._POOL_TOTAL
        db._POOL_IDLE, db._POOL_TOTAL = db.OrderedDict(), 0
    def tearDown(self):
        db._POOL_IDLE, db._POOL_TOTAL = self.old_idle, self.old_total

    def test_concurrent_tenants_do_not_exceed_process_budget(self):
        peak = 0
        created = []
        lock = threading.Lock()
        def factory(database):
            nonlocal peak
            with lock:
                created.append(FakeConnection(database))
                peak = max(peak, sum(not item.closed for item in created))
                return created[-1]
        def request(index):
            database = 'tenant_' + str(index % 20)
            raw, release = db._borrow(database)
            self.assertEqual(raw.database, database)
            time.sleep(.001)
            release(raw)
        with patch.dict(os.environ, {'ONTOLOGY_DB_POOL_SIZE': '4'}), patch.object(db, '_raw_connection', factory):
            with ThreadPoolExecutor(max_workers=16) as pool:
                list(pool.map(request, range(120)))
        self.assertLessEqual(peak, 4)
        self.assertEqual(db._POOL_TOTAL, len(db._POOL_IDLE))
        self.assertEqual(db._POOL_TOTAL, 4)

    def test_connection_failure_returns_budget_and_reuse_keeps_database(self):
        with patch.dict(os.environ, {'ONTOLOGY_DB_POOL_SIZE': '1'}):
            with patch.object(db, '_raw_connection', side_effect=db.pymysql.OperationalError('offline')):
                with self.assertRaises(db.pymysql.OperationalError): db._borrow('tenant_a')
            self.assertEqual(db._POOL_TOTAL, 0)
            with patch.object(db, '_raw_connection', side_effect=FakeConnection):
                first, release = db._borrow('tenant_a'); release(first)
                reused, release = db._borrow('tenant_a'); release(reused)
                self.assertIs(first, reused)
                other, release = db._borrow('tenant_b'); release(other)
                self.assertTrue(first.closed)
                self.assertEqual(other.database, 'tenant_b')
                self.assertEqual(db._POOL_TOTAL, 1)

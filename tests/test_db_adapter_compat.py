"""Preserve legacy row/query behavior while reducing repeated conversion work."""
import unittest

from backend.db import Cursor, Row, translate


class AdapterCompatibility(unittest.TestCase):
    def test_row_preserves_named_numeric_slice_and_value_iteration(self):
        source = {'id': 'A', 'nullable': None, 'number': 7}
        row = Row(source)
        self.assertEqual(row['id'], 'A')
        self.assertEqual(row[0], 'A')
        self.assertEqual(row[-1], 7)
        self.assertEqual(row[1:], [None, 7])
        self.assertEqual(list(row), ['A', None, 7])
        # Positional values historically capture the original result row.
        row['id'] = 'B'
        self.assertEqual(row[0], 'A')
        self.assertEqual(row.copy(), {'id': 'B', 'nullable': None, 'number': 7})
        self.assertEqual(row.copy(), dict(row))
        self.assertIs(type(row.copy()), dict)
        self.assertEqual(source['id'], 'A')

    def test_partial_cursor_iteration_preserves_position(self):
        class DriverCursor:
            rows = iter([{'id': 'A'}, {'id': 'B'}, {'id': 'C'}])
            def fetchone(self):
                return next(self.rows, None)
        cursor = Cursor(DriverCursor())
        iterator = iter(cursor)
        self.assertEqual(next(iterator)[0], 'A')
        self.assertEqual(cursor.fetchone()['id'], 'B')
        self.assertEqual(next(iterator)['id'], 'C')
        self.assertEqual(list(iterator), [])

    def test_cached_translation_preserves_quoted_parameters_and_percents(self):
        translate.cache_clear()
        sql = "SELECT '?' AS marker FROM entities WHERE title LIKE 'rate%?' AND id=?"
        expected = "SELECT '?' AS marker FROM entities WHERE title LIKE 'rate%%?' AND id=%s"
        self.assertEqual(translate(sql), expected)
        self.assertEqual(translate(sql), expected)
        self.assertEqual(translate.cache_info().hits, 1)
        other = 'SELECT * FROM entities WHERE id=? AND kind=?'
        self.assertEqual(translate(other), 'SELECT * FROM entities WHERE id=%s AND kind=%s')
        self.assertEqual(translate.cache_info().maxsize, 2048)


if __name__ == '__main__':
    unittest.main()

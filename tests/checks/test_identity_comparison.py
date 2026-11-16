"""Pure cleanup-boundary tests; no MySQL connection or release process starts."""

import hashlib
import unittest
from unittest.mock import MagicMock, patch

from e2e.run import new_namespace
from scripts.drills.compare_identity_v070 import comparison_scope


class IdentityComparisonScope(unittest.TestCase):
    def setUp(self) -> None:
        self.namespace = "e2e_r1_" + "a" * 24
        self.scope = comparison_scope({}, self.namespace)

    def test_exact_four_names_match_fixture_derivation(self) -> None:
        control = self.namespace + "_control"
        expected = {control} | {
            self.namespace[:32] + "_t_" + hashlib.sha256((control + ":" + tenant).encode()).hexdigest()[:24]
            for tenant in ("hq", "dept-01", "sub-01")
        }
        self.assertEqual(self.scope.candidates, expected)
        self.assertEqual(len(expected), 4)
        self.assertNotIn(self.namespace, expected)

    def test_namespace_preserves_all_96_random_bits_without_prefix_truncation(self) -> None:
        with patch("e2e.run.secrets.token_hex", return_value="a" * 24) as token:
            namespace = new_namespace()
        token.assert_called_once_with(12)
        self.assertEqual(namespace, self.namespace)
        self.assertLessEqual(len(namespace), 32)
        other = "e2e_r1_" + "a" * 23 + "b"
        self.assertFalse(self.scope.candidates & comparison_scope({}, other).candidates)
        with self.assertRaises(ValueError):
            comparison_scope({}, "ontology_r17compare_" + "a" * 24)

    def test_collision_never_arms_loads_or_drops(self) -> None:
        loader = MagicMock()
        with (
            patch.object(self.scope, "_existing", return_value={next(iter(self.scope.candidates))}),
            patch("e2e.run.pymysql.connect") as connect,
            self.assertRaises(ValueError),
            self.scope,
        ):
            loader()
        self.assertFalse(self.scope.armed)
        self.assertEqual(self.scope.created, set())
        self.assertEqual(self.scope.cleaned, set())
        loader.assert_not_called()
        connect.assert_not_called()

    def test_unknown_same_prefix_discovery_refuses_every_drop(self) -> None:
        known = next(iter(self.scope.candidates))
        with (
            patch.object(self.scope, "_existing", side_effect=[set(), {known, self.namespace + "_unowned"}]),
            patch("e2e.run.pymysql.connect") as connect,
            self.assertRaisesRegex(RuntimeError, "unowned"),
            self.scope,
        ):
            pass
        connect.assert_not_called()
        self.assertEqual(self.scope.cleaned, set())

    def test_partial_loader_failure_cleans_only_reserved_existing_names(self) -> None:
        created = sorted(self.scope.candidates)[:2]
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        with (
            patch.object(self.scope, "_existing", side_effect=[set(), set(created)]),
            patch("e2e.run.pymysql.connect", return_value=connection),
            self.assertRaisesRegex(RuntimeError, "loader failed"),
            self.scope,
        ):
            raise RuntimeError("loader failed after two databases")
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in cursor.execute.call_args_list], [f"DROP DATABASE `{n}`" for n in created]
        )
        self.assertEqual(self.scope.created, set(created))
        self.assertEqual(self.scope.cleaned, set(created))

    def test_discovery_queries_exact_candidates_never_prefix(self) -> None:
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        with patch("e2e.run.pymysql.connect", return_value=connection):
            self.assertEqual(self.scope._existing(), set())
        query, parameters = cursor.execute.call_args.args
        self.assertIn("SCHEMA_NAME IN (%s,%s,%s,%s)", query)
        self.assertNotIn("LIKE", query)
        self.assertNotIn("LEFT", query)
        self.assertEqual(parameters, sorted(self.scope.candidates))

    def test_unreserved_valid_tenant_hash_never_reaches_catalog_expansion(self) -> None:
        unknown = self.namespace + "_t_" + "f" * 24
        reserved = set(self.scope.candidates)
        self.assertNotIn(unknown, reserved)
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [(unknown,)]
        self.scope.armed = True
        with (
            patch("e2e.run.pymysql.connect", return_value=connection),
            patch.object(self.scope, "_catalog_databases", return_value={unknown}) as catalog,
            self.assertRaisesRegex(RuntimeError, "unowned"),
        ):
            self.scope.record_created()
        catalog.assert_not_called()
        self.assertEqual(self.scope.candidates, reserved)
        self.assertEqual(self.scope.created, set())
        self.assertEqual(self.scope.cleaned, set())

# Copyright 2021-present StarRocks, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Materialized view comparison in autogenerate."""

from unittest.mock import Mock

from alembic.operations import ops
from sqlalchemy import MetaData, Table, testing
from sqlalchemy.testing import eq_, expect_raises, fixtures

from starrocks.alembic.compare import UnalterableChange, _compare_mv, _compare_mv_definition
from starrocks.alembic.ops import AlterMaterializedViewOp, CreateMaterializedViewOp, DropMaterializedViewOp
from starrocks.common.params import DialectName, TableInfoKey, TableKind, TableObjectInfoKey
from starrocks.sql.schema import MaterializedView


class CompareMaterializedViewTest(fixtures.TestBase):
    """``_compare_mv``: refresh and properties are altered in place; other changes are refused."""

    __requires__ = ("sqlalchemy_2",)

    def setup_test(self):
        self.mock_autogen_context = Mock()
        self.mock_autogen_context.dialect = Mock()
        self.mock_autogen_context.dialect.name = DialectName
        self.mock_autogen_context.dialect.default_schema_name = None

    def _create_reflected_mv(
        self,
        name: str,
        definition: str,
        comment: str = None,
        partition_by: str = None,
        distributed_by: str = None,
        order_by: str = None,
        refresh: str = None,
        properties: dict = None,
        schema: str = None,
    ) -> Table:
        """Helper to create a Table object simulating a reflected MV."""
        metadata = MetaData()
        # Reflected MVs are represented as Table objects. MV is also OK.
        # table = MaterializedView(name, metadata, schema=schema, definition=definition)
        table = Table(name, metadata, schema=schema)
        table.info[TableObjectInfoKey.DEFINITION] = definition
        table.info[TableObjectInfoKey.TABLE_KIND] = TableKind.MATERIALIZED_VIEW

        if comment:
            table.comment = comment

        dialect_opts = table.dialect_options[DialectName]
        if partition_by:
            dialect_opts[TableInfoKey.PARTITION_BY] = partition_by
        if distributed_by:
            dialect_opts[TableInfoKey.DISTRIBUTED_BY] = distributed_by
        if order_by:
            dialect_opts[TableInfoKey.ORDER_BY] = order_by
        if refresh:
            dialect_opts[TableInfoKey.REFRESH] = refresh
        if properties:
            dialect_opts[TableInfoKey.PROPERTIES] = properties

        return table

    # ========================================================================
    # ALTER MV - Mutable Attributes (Refresh & Properties)
    # ========================================================================

    def test_alter_mv_refresh_changed(self):
        """ALTER: Refresh strategy changed (generates AlterMaterializedViewOp)."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1", refresh="ASYNC")
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1", starrocks_refresh="MANUAL")

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_(len(upgrade_ops.ops), 1)
        op = upgrade_ops.ops[0]
        assert isinstance(op, AlterMaterializedViewOp)
        eq_(op.view_name, "my_mv")
        eq_(op.refresh, "MANUAL")
        eq_(op.existing_refresh, "ASYNC")
        # Other attributes should be None as they are not changed
        assert op.properties is None
        assert op.existing_properties is None

    def test_spacing_around_parentheses_is_no_refresh_change(self):
        """SHOW CREATE writes EVERY(INTERVAL ...), the model may write EVERY (INTERVAL ...)."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv(
            "my_mv", definition="SELECT 1", refresh="DEFERRED ASYNC EVERY(INTERVAL 2 HOUR)"
        )
        meta_mv = MaterializedView(
            "my_mv", MetaData(), definition="SELECT 1", starrocks_refresh="DEFERRED ASYNC EVERY ( INTERVAL 2 HOUR )"
        )

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_(upgrade_ops.ops, [])

    def test_an_omitted_refresh_is_left_alone(self):
        """An MV with a distribution may omit its refresh; StarRocks then reports MANUAL, which is no change."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1", distributed_by="RANDOM", refresh="MANUAL")
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1", starrocks_distributed_by="RANDOM")

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_(upgrade_ops.ops, [])

    def test_a_refresh_the_database_does_not_report_has_no_downgrade_value(self):
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1")
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1", starrocks_refresh="MANUAL")

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_([(op.refresh, op.existing_refresh) for op in upgrade_ops.ops], [("MANUAL", None)])

    def test_alter_mv_properties_changed(self):
        """ALTER: Properties changed (generates AlterMaterializedViewOp)."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv(
            "my_mv", definition="SELECT 1", properties={"mv_rewrite_staleness_second": "0"}
        )
        meta_mv = MaterializedView(
            "my_mv", MetaData(), definition="SELECT 1", starrocks_properties={"mv_rewrite_staleness_second": "60"}
        )

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_(len(upgrade_ops.ops), 1)
        op = upgrade_ops.ops[0]
        assert isinstance(op, AlterMaterializedViewOp)
        eq_(op.properties, {"mv_rewrite_staleness_second": "60"})
        eq_(op.existing_properties, {"mv_rewrite_staleness_second": "0"})
        assert op.refresh is None

    # ========================================================================
    # DROP/CREATE MV - Immutable Attributes
    # ========================================================================

    @staticmethod
    def _assert_recreated(upgrade_ops, caplog, reason):
        """The materialized view is dropped and created again, and the warning names the change behind it."""
        eq_([type(op) for op in upgrade_ops.ops], [DropMaterializedViewOp, CreateMaterializedViewOp])
        assert "drops it and creates it again" in caplog.text
        assert reason in caplog.text

    def test_modify_mv_definition_changed(self, caplog):
        """DROP/CREATE: Definition changed."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1")
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 2")

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)
        self._assert_recreated(upgrade_ops, caplog, "does not support altering MV definition")

    @testing.combinations(
        (None, {"mv_rewrite_staleness_second": "60", "replication_num": "1"}, {"partition_ttl_number": "5"}),
        ("dt", {"mv_rewrite_staleness_second": "60", "replication_num": "1", "partition_ttl_number": "5"}, {}),
        argnames="model_partition_by, kept, lost",
    )
    def test_a_recreated_mv_keeps_the_properties_the_model_leaves_out(self, caplog, model_partition_by, kept, lost):
        """Properties other than StarRocks' defaults carry over, except those StarRocks takes only when partitioned."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv(
            "my_mv",
            definition="SELECT 1",
            partition_by="dt",
            properties={
                "mv_rewrite_staleness_second": "60",
                "replication_num": "1",
                "storage_medium": "HDD",
                "partition_ttl_number": "5",
            },
        )
        partitioning = {"starrocks_partition_by": model_partition_by} if model_partition_by else {}
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 2", **partitioning)

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)
        eq_(upgrade_ops.ops[1].kwargs["starrocks_properties"], kept)
        eq_(f"loses the database's properties {lost!r}" in caplog.text, bool(lost))

    def test_modify_mv_partition_by_changed(self, caplog):
        """DROP/CREATE: Partition by changed."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1", partition_by="date_trunc('day', dt)")
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1", starrocks_partition_by="dt")

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)
        self._assert_recreated(upgrade_ops, caplog, "does not support 'ALTER MATERIALIZED VIEW PARTITION BY'")

    def test_modify_mv_distributed_by_changed(self, caplog):
        """DROP/CREATE: Distributed by changed."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1", distributed_by="HASH(id)")
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1", starrocks_distributed_by="RANDOM")

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)
        self._assert_recreated(upgrade_ops, caplog, "does not support 'ALTER MATERIALIZED VIEW DISTRIBUTED BY'")

    @testing.combinations(
        ("explicit_bucket_count", "RANDOM BUCKETS 10"),
        ("older_default_hash_on_first_column", "HASH(id)"),
        ("default_random", "RANDOM"),
        argnames="conn_distribution",
        id_="ia",
    )
    def test_no_change_mv_distribution_omitted(self, conn_distribution):
        """No change: metadata that omits DISTRIBUTED BY must not diff or crash.

        StarRocks always auto-assigns a distribution and SHOW CREATE renders it (e.g.
        ``DISTRIBUTED BY RANDOM BUCKETS 10`` or a defaulted ``HASH(...)``). When the user does
        not declare distribution in metadata, the reflected value must be left untouched — not
        compared against the default and 'reset', which raises for this immutable attribute.
        """
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1", distributed_by=conn_distribution)
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1")  # no starrocks_distributed_by

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_(len(upgrade_ops.ops), 0)

    def test_modify_mv_order_by_changed(self, caplog):
        """DROP/CREATE: Order by changed."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1", order_by="id")
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1", starrocks_order_by="name")

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)
        self._assert_recreated(upgrade_ops, caplog, "does not support 'ALTER MATERIALIZED VIEW ORDER BY'")

    # ========================================================================
    # No-Change Tests
    # ========================================================================

    def test_no_change_mv(self):
        """No change: Identical MVs."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv(
            name="my_mv",
            definition="SELECT id, name FROM users",
            comment="Test comment",
            partition_by="id",
            distributed_by="HASH(id)",
            order_by="name",
            refresh="ASYNC",
            properties={"replication_num": "1"},
        )
        meta_mv = MaterializedView(
            "my_mv",
            MetaData(),
            definition="SELECT id, name FROM users",
            comment="Test comment",
            starrocks_partition_by="id",
            starrocks_distributed_by="HASH(id)",
            starrocks_order_by="name",
            starrocks_refresh="ASYNC",
            starrocks_properties={"replication_num": "1"},
        )

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_(len(upgrade_ops.ops), 0)

    def test_no_change_mv_sql_normalization(self):
        """No change: SQL definition differs only by formatting."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv(name="my_mv", definition="SELECT 1 AS `val`")
        meta_mv = MaterializedView("my_mv", MetaData(), definition="select 1 as val")

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_(len(upgrade_ops.ops), 0)

    @testing.combinations(
        # StarRocks 4.1+ reflects a periodic async refresh with the SCHEDULE keyword,
        # while metadata declares it as ASYNC. These must be treated as equivalent.
        ("SCHEDULE EVERY(INTERVAL 10 MINUTE)", "ASYNC EVERY(INTERVAL 10 MINUTE)"),
        # START() quoting differs between SHOW CREATE (double) and metadata (single).
        (
            'SCHEDULE START("2024-01-01 10:00:00") EVERY(INTERVAL 1 HOUR)',
            "ASYNC START('2024-01-01 10:00:00') EVERY(INTERVAL 1 HOUR)",
        ),
        # Case/whitespace only differences.
        ("async every(interval 10 minute)", "ASYNC EVERY(INTERVAL 10 MINUTE)"),
        # SCHEDULE may appear after an IMMEDIATE/DEFERRED moment prefix. StarRocks 4.1 emits
        # "REFRESH DEFERRED SCHEDULE EVERY(...)" for a deferred scheduled MV, which reflection
        # canonicalizes to "DEFERRED ASYNC ..."; metadata using the 4.1 SCHEDULE spelling must
        # still be treated as equivalent.
        ("DEFERRED ASYNC EVERY(INTERVAL 1 HOUR)", "DEFERRED SCHEDULE EVERY(INTERVAL 1 HOUR)"),
        ("IMMEDIATE ASYNC EVERY(INTERVAL 1 HOUR)", "IMMEDIATE SCHEDULE EVERY(INTERVAL 1 HOUR)"),
        # Both sides using the SCHEDULE spelling with a moment prefix.
        ("DEFERRED SCHEDULE EVERY(INTERVAL 30 MINUTE)", "DEFERRED SCHEDULE EVERY(INTERVAL 30 MINUTE)"),
        # IMMEDIATE is the default refresh moment and StarRocks never renders it in SHOW CREATE
        # (only DEFERRED is emitted), so reflection yields no moment. Metadata that explicitly
        # declares IMMEDIATE must still be treated as equivalent to the reflected form.
        ("ASYNC EVERY(INTERVAL 1 HOUR)", "IMMEDIATE ASYNC EVERY(INTERVAL 1 HOUR)"),
        ("ASYNC EVERY(INTERVAL 1 HOUR)", "IMMEDIATE SCHEDULE EVERY(INTERVAL 1 HOUR)"),
        ("ASYNC", "IMMEDIATE ASYNC"),
        # StarRocks keeps a START time in full: midnight for a date alone, and seconds for a time without them.
        ('ASYNC START("2026-10-25 00:00:00") EVERY(INTERVAL 1 DAY)', "ASYNC START('2026-10-25') EVERY (INTERVAL 1 DAY)"),
        ('ASYNC START("2026-10-25 10:30:00") EVERY(INTERVAL 1 HOUR)', "ASYNC START('2026-10-25 10:30') EVERY(INTERVAL 1 HOUR)"),
        argnames="conn_refresh, meta_refresh",
    )
    def test_no_change_mv_refresh_schedule_alias(self, conn_refresh, meta_refresh):
        """No change: 4.1 SCHEDULE refresh keyword is equivalent to metadata ASYNC."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1", refresh=conn_refresh)
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1", starrocks_refresh=meta_refresh)

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_(len(upgrade_ops.ops), 0)

    def test_another_refresh_start_is_a_change(self):
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv(
            "my_mv", definition="SELECT 1", refresh='ASYNC START("2026-10-25 00:00:00") EVERY(INTERVAL 1 DAY)'
        )
        meta_mv = MaterializedView(
            "my_mv", MetaData(), definition="SELECT 1", starrocks_refresh="ASYNC START('2026-10-26') EVERY(INTERVAL 1 DAY)"
        )

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_(len(upgrade_ops.ops), 1)


    @testing.combinations(
        ("DEFERRED ASYNC", "ASYNC"),
        ("DEFERRED ASYNC EVERY(INTERVAL 1 HOUR)", "ASYNC EVERY(INTERVAL 1 HOUR)"),
        ("DEFERRED MANUAL", "MANUAL"),
        ("DEFERRED ASYNC", "DEFERRED ASYNC"),
        ("ASYNC", "ASYNC"),
        argnames="conn_refresh, meta_refresh",
    )
    def test_no_change_when_model_states_no_refresh_moment(self, conn_refresh, meta_refresh):
        """ALTER ... REFRESH ASYNC leaves a DEFERRED view deferred, so a model that states no moment is unchanged."""
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1", refresh=conn_refresh)
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1", starrocks_refresh=meta_refresh)

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_(len(upgrade_ops.ops), 0)

    @testing.combinations(
        ("DEFERRED ASYNC", "IMMEDIATE ASYNC", "from DEFERRED to IMMEDIATE"),
        ("ASYNC", "DEFERRED ASYNC", "from IMMEDIATE to DEFERRED"),
        argnames="conn_refresh, meta_refresh, reason",
    )
    def test_refresh_moment_the_model_states_is_recreated(self, caplog, conn_refresh, meta_refresh, reason):
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1", refresh=conn_refresh)
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1", starrocks_refresh=meta_refresh)

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        self._assert_recreated(upgrade_ops, caplog, reason)

    def test_refresh_scheme_change_on_a_deferred_view_is_altered(self):
        upgrade_ops = ops.UpgradeOps([])
        conn_mv = self._create_reflected_mv("my_mv", definition="SELECT 1", refresh="DEFERRED MANUAL")
        meta_mv = MaterializedView("my_mv", MetaData(), definition="SELECT 1", starrocks_refresh="ASYNC")

        _compare_mv(self.mock_autogen_context, upgrade_ops, None, "my_mv", conn_mv, meta_mv)

        eq_([type(op) for op in upgrade_ops.ops], [AlterMaterializedViewOp])
        eq_(upgrade_ops.ops[0].refresh, "ASYNC")


def _connection_without_temp_views():
    """A connection on which every temporary view fails to be created, as for a user without CREATE VIEW."""
    def execute(statement, *args):
        if statement.startswith("CREATE"):
            raise RuntimeError("Access denied")
        return Mock()

    return Mock(exec_driver_sql=Mock(side_effect=execute))


class MaterializedViewDefinitionWithoutTempViewTest(fixtures.TestBase):
    """Without the temporary view, a difference only the in-process normalizer sees is a change only sometimes."""

    __requires__ = ("sqlalchemy_2",)

    @staticmethod
    def _compare(server_version, model_definition, revision_creates=frozenset()):
        database_mv = MaterializedView("mv", MetaData(), definition="SELECT `db`.`t`.`id` FROM `db`.`t`")
        model_mv = MaterializedView("mv", MetaData(), definition=model_definition)
        _compare_mv_definition(
            [], "db", "mv", database_mv, model_mv,
            conn=_connection_without_temp_views(), server_version=server_version,
            revision_creates=set(revision_creates), default_schema="db",
        )

    def test_from_4_0_6_a_text_difference_is_a_change(self):
        with expect_raises(UnalterableChange):
            self._compare((4, 1, 6), "SELECT id FROM t")

    def test_before_4_0_6_a_definition_reading_a_new_table_is_a_change(self):
        with expect_raises(UnalterableChange):
            self._compare((3, 3, 22), "SELECT id FROM t_new", {(None, "t_new")})

    @testing.combinations(((3, 3, 22),), ((4, 0, 5),), (None,), argnames="server_version")
    def test_before_4_0_6_any_other_text_difference_is_warned_about(self, server_version, caplog):
        self._compare(server_version, "SELECT id FROM t", {(None, "t_other")})
        assert "could not canonicalize them through a temporary view" in caplog.text

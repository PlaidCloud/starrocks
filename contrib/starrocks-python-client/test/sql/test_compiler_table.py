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

"""CREATE TABLE compilation, with StarRocks key, partition, distribution, ORDER BY and PROPERTIES clauses.

The dialect puts each column on its own indented line and each table clause on its own line; ``assert_compile``
drops the line breaks, so each expected string literal below is one line of the generated DDL.
"""

import logging

from sqlalchemy import (
    BigInteger,
    Column,
    Computed,
    Date,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    exc,
    func,
    testing,
)
from sqlalchemy.schema import CreateIndex, CreateTable, DropIndex
from sqlalchemy.testing import expect_raises_message, fixtures
from sqlalchemy.testing.assertions import AssertsCompiledSQL

from starrocks.common.params import ColumnAggInfoKeyWithPrefix
from starrocks.common.types import ColumnAggType
from starrocks.datatype import DOUBLE
from starrocks.dialect import StarRocksDialect


class CreateTableCompileTest(fixtures.TestBase, AssertsCompiledSQL):
    __dialect__ = StarRocksDialect()

    def test_engine_and_comment(self):
        """``ENGINE = OLAP`` is StarRocks' default, so the dialect leaves it out."""
        tbl = Table(
            "engine_comment_tbl",
            MetaData(),
            Column("k1", Integer),
            starrocks_engine="OLAP",
            comment="A simple table comment.",
            starrocks_duplicate_key="k1",
            starrocks_distributed_by="HASH(k1)",
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE engine_comment_tbl ("
            "    k1 INTEGER"
            ")"
            "DUPLICATE KEY (k1)"
            "COMMENT 'A simple table comment.'"
            "DISTRIBUTED BY HASH(k1)",
        )

    @testing.combinations(
        ("starrocks_duplicate_key", "DUPLICATE KEY (k1)"),
        ("starrocks_aggregate_key", "AGGREGATE KEY (k1)"),
        ("starrocks_unique_key", "UNIQUE KEY (k1)"),
        ("starrocks_primary_key", "PRIMARY KEY (k1)"),
        argnames="key_kwarg, key_clause",
        id_="sa",
    )
    def test_key_description(self, key_kwarg, key_clause):
        tbl = Table(
            "key_tbl", MetaData(), Column("k1", Integer), starrocks_distributed_by="HASH(k1)", **{key_kwarg: "k1"}
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE key_tbl ("
            "    k1 INTEGER"
            ")"
            f"{key_clause}"
            "DISTRIBUTED BY HASH(k1)",
        )

    def test_column_comment(self):
        tbl = Table(
            "col_comment_tbl",
            MetaData(),
            Column("k1", Integer, comment="This is a column comment."),
            starrocks_distributed_by="HASH(k1)",
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE col_comment_tbl ("
            "    k1 INTEGER COMMENT 'This is a column comment.'"
            ")"
            "DISTRIBUTED BY HASH(k1)",
        )

    def test_column_attributes(self):
        tbl = Table(
            "col_attr_tbl",
            MetaData(),
            Column("k1", Integer, primary_key=True, autoincrement=False),
            Column("k2", BigInteger, autoincrement=True),
            Column("k3", String(50), nullable=True),
            starrocks_distributed_by="HASH(k1)",
            starrocks_primary_key="k1",
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE col_attr_tbl ("
            "    k1 INTEGER NOT NULL, "
            "    k2 BIGINT NOT NULL AUTO_INCREMENT, "
            "    k3 VARCHAR(50)"
            ")"
            "PRIMARY KEY (k1)"
            "DISTRIBUTED BY HASH(k1)",
        )

    def test_aggregate_key_table(self):
        tbl = Table(
            "agg_tbl",
            MetaData(),
            Column("k1", Integer),
            Column("v1", Integer, **{ColumnAggInfoKeyWithPrefix.AGG_TYPE: ColumnAggType.SUM}),
            Column("v2", String(50), **{ColumnAggInfoKeyWithPrefix.AGG_TYPE: ColumnAggType.REPLACE}),
            starrocks_AGGREGATE_KEY="k1",
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE agg_tbl ("
            "    k1 INTEGER, "
            "    v1 INTEGER SUM, "
            "    v2 VARCHAR(50) REPLACE"
            ")"
            "AGGREGATE KEY (k1)",
        )

    @testing.combinations(
        ("percentile_union", "PERCENTILE_UNION", "v1 INTEGER PERCENTILE_UNION"),
        ("state_function", "sum(BIGINT)", "v1 INTEGER sum(BIGINT)"),
        argnames="agg_type, column_text",
        id_="iaa",
    )
    def test_aggregate_type_forms(self, agg_type, column_text):
        tbl = Table(
            "agg_forms_tbl",
            MetaData(),
            Column("k1", Integer),
            Column("v1", Integer, **{ColumnAggInfoKeyWithPrefix.AGG_TYPE: agg_type}),
            starrocks_aggregate_key="k1",
        )
        self.assert_compile(
            CreateTable(tbl), "CREATE TABLE agg_forms_tbl (" "    k1 INTEGER, " f"    {column_text}" ")" "AGGREGATE KEY (k1)"
        )

    @testing.combinations(("count", "COUNT"), ("unknown", "AVERAGE"), argnames="agg_type", id_="ia")
    def test_unknown_aggregate_type_raises(self, agg_type):
        tbl = Table(
            "agg_unknown_tbl",
            MetaData(),
            Column("k1", Integer),
            Column("v1", Integer, **{ColumnAggInfoKeyWithPrefix.AGG_TYPE: agg_type}),
            starrocks_aggregate_key="k1",
        )
        with expect_raises_message(exc.CompileError, f"Unsupported aggregate type for column 'v1': {agg_type}"):
            CreateTable(tbl).compile(dialect=self.__dialect__)

    def test_aggregate_key_column_cannot_be_aggregated(self):
        tbl = Table(
            "invalid_agg_tbl1",
            MetaData(),
            Column(
                "k1",
                Integer,
                **{ColumnAggInfoKeyWithPrefix.IS_AGG_KEY: True, ColumnAggInfoKeyWithPrefix.AGG_TYPE: ColumnAggType.SUM},
            ),
            starrocks_aggregate_key="k1",
        )
        with expect_raises_message(exc.CompileError, "cannot be both KEY and aggregated"):
            CreateTable(tbl).compile(dialect=self.__dialect__)

    def test_aggregate_type_needs_aggregate_key_table(self):
        tbl = Table(
            "invalid_agg_tbl2",
            MetaData(),
            Column("k1", Integer),
            Column("v1", Integer, **{ColumnAggInfoKeyWithPrefix.AGG_TYPE: ColumnAggType.SUM}),
            starrocks_duplicate_key="k1",
        )
        with expect_raises_message(exc.CompileError, "only valid for AGGREGATE KEY tables"):
            CreateTable(tbl).compile(dialect=self.__dialect__)

    @testing.combinations(
        ("string", "event_date, user_id"),
        ("parenthesised_string", "(event_date, user_id)"),
        ("list", ["event_date", "user_id"]),
        ("tuple", ("event_date", "user_id")),
        argnames="order_by",
        id_="ia",
    )
    def test_order_by_forms(self, order_by):
        """``starrocks_order_by`` takes a column list string, or a list of columns as ALTER TABLE ORDER BY does."""
        tbl = Table(
            "ordered_tbl",
            MetaData(),
            Column("user_id", Integer),
            Column("event_date", Date),
            starrocks_duplicate_key="user_id",
            starrocks_order_by=order_by,
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE ordered_tbl ("
            "    user_id INTEGER, "
            "    event_date DATE"
            ")"
            "DUPLICATE KEY (user_id)"
            "ORDER BY (event_date, user_id)",
        )

    def test_column_default_value(self):
        tbl = Table(
            "default_val_tbl",
            MetaData(),
            Column("k1", Integer),
            Column("k2", String(50), server_default="foo"),
            starrocks_distributed_by="HASH(k1)",
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE default_val_tbl ("
            "    k1 INTEGER, "
            "    k2 VARCHAR(50) DEFAULT 'foo'"
            ")"
            "DISTRIBUTED BY HASH(k1)",
        )

    def test_generated_column(self):
        tbl = Table(
            "generated_col_tbl",
            MetaData(),
            Column("k1", Integer),
            Column("k2", String(50)),
            Column("k3", Integer, Computed("left(k2, 10)")),
            starrocks_distributed_by="HASH(k1)",
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE generated_col_tbl ("
            "    k1 INTEGER, "
            "    k2 VARCHAR(50), "
            "    k3 INTEGER AS (left(k2, 10))"
            ")"
            "DISTRIBUTED BY HASH(k1)",
        )

    @testing.combinations(
        (
            "range",
            [Column("event_date", Date)],
            'RANGE(event_date) (PARTITION p2023 VALUES LESS THAN ("2024-01-01"))',
            "    event_date DATE",
        ),
        (
            "range_multi_column",
            [Column("k1", Integer), Column("k2", Integer)],
            "RANGE(k1, k2) (PARTITION p1 VALUES LESS THAN (100, 200))",
            "    k1 INTEGER,     k2 INTEGER",
        ),
        (
            "list",
            [Column("city", String(50))],
            'LIST(city) (PARTITION p_north VALUES IN ("Beihai", "Dalian"))',
            "    city VARCHAR(50)",
        ),
        (
            "date_trunc_recognised_as_range",
            [Column("event_time", DateTime)],
            "date_trunc('month', event_time)",
            "    event_time DATETIME",
        ),
        (
            "expression",
            [Column("event_time", DateTime)],
            "to_date(event_time)",
            "    event_time DATETIME",
        ),
        (
            "expression_multi",
            [Column("event_time", DateTime), Column("id", Integer)],
            "date_trunc('day', event_time), id % 10",
            "    event_time DATETIME,     id INTEGER",
        ),
        (
            "batch",
            [Column("datekey", Integer)],
            'RANGE(datekey) (START ("1") END ("5") EVERY (1))',
            "    datekey INTEGER",
        ),
        argnames="columns, partition_by, column_lines",
        id_="iaaa",
    )
    def test_partition_description(self, columns, partition_by, column_lines):
        """The partition clause is passed through as given.

        Keep the ``date_trunc`` case: StarRocks recognises it as a RANGE partition, unlike other expressions.
        """
        tbl = Table("p_tbl", MetaData(), *columns, starrocks_partition_by=partition_by)
        self.assert_compile(
            CreateTable(tbl),
            f"CREATE TABLE p_tbl ({column_lines})PARTITION BY {partition_by}",
        )

    @testing.combinations(
        ("hash", Column("user_id", Integer), "HASH(user_id) BUCKETS 10", "    user_id INTEGER"),
        ("random", Column("log_id", BigInteger), "RANDOM BUCKETS 4", "    log_id BIGINT"),
        argnames="column, distributed_by, column_line",
        id_="iaaa",
    )
    def test_distribution_description(self, column, distributed_by, column_line):
        tbl = Table("dist_tbl", MetaData(), column, starrocks_distributed_by=distributed_by)
        self.assert_compile(
            CreateTable(tbl),
            f"CREATE TABLE dist_tbl ({column_line})DISTRIBUTED BY {distributed_by}",
        )

    @testing.combinations(
        ("range", "RANGE"), ("lowercase", "range"), ("buckets", "RANGE BUCKETS 4"), argnames="distributed_by", id_="ia"
    )
    def test_range_distribution_omits_clause(self, distributed_by, caplog):
        """RANGE has no DISTRIBUTED BY syntax: StarRocks applies it when the clause is omitted."""
        tbl = Table(
            "dist_range_tbl",
            MetaData(),
            Column("k1", Integer),
            Column("v", Integer),
            starrocks_duplicate_key="k1",
            starrocks_distributed_by=distributed_by,
        )
        with caplog.at_level(logging.WARNING, logger="starrocks.dialect"):
            self.assert_compile(
                CreateTable(tbl),
                "CREATE TABLE dist_range_tbl ("
                "    k1 INTEGER, "
                "    v INTEGER"
                ")"
                "DUPLICATE KEY (k1)",
            )
        assert ("BUCKETS is ignored" in caplog.text) == ("BUCKETS" in distributed_by)
        assert "requires a key type or ORDER BY" not in caplog.text

    def test_range_distribution_with_order_by(self, caplog):
        tbl = Table(
            "dist_range_order_tbl",
            MetaData(),
            Column("k1", Integer),
            starrocks_order_by="k1",
            starrocks_distributed_by="RANGE",
        )
        with caplog.at_level(logging.WARNING, logger="starrocks.dialect"):
            self.assert_compile(
                CreateTable(tbl),
                "CREATE TABLE dist_range_order_tbl ("
                "    k1 INTEGER"
                ")"
                "ORDER BY (k1)",
            )
        assert "requires a key type or ORDER BY" not in caplog.text

    def test_range_distribution_without_key_warns(self, caplog):
        """Without a key type or ORDER BY, StarRocks falls back to RANDOM distribution."""
        tbl = Table("dist_range_nokey_tbl", MetaData(), Column("k1", Integer), starrocks_distributed_by="RANGE")
        with caplog.at_level(logging.WARNING, logger="starrocks.dialect"):
            self.assert_compile(
                CreateTable(tbl),
                "CREATE TABLE dist_range_nokey_tbl ("
                "    k1 INTEGER"
                ")",
            )
        assert "requires a key type or ORDER BY" in caplog.text

    def test_order_by(self):
        tbl = Table(
            "orderby_tbl",
            MetaData(),
            Column("k1", Integer),
            Column("k2", String(50)),
            starrocks_order_by="k1, k2",
            starrocks_distributed_by="HASH(k1)",
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE orderby_tbl ("
            "    k1 INTEGER, "
            "    k2 VARCHAR(50)"
            ")"
            "DISTRIBUTED BY HASH(k1)"
            "ORDER BY (k1, k2)",
        )

    def test_properties(self):
        tbl = Table(
            "props_tbl",
            MetaData(),
            Column("k1", Integer),
            starrocks_properties={"replication_num": "1", "storage_medium": "SSD"},
            starrocks_distributed_by="HASH(k1)",
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE props_tbl ("
            "    k1 INTEGER"
            ")"
            "DISTRIBUTED BY HASH(k1)"
            "PROPERTIES("
            '    "replication_num"="1",'
            '    "storage_medium"="SSD"'
            ")",
        )

    def test_comprehensive_table(self):
        tbl = Table(
            "comprehensive_table",
            MetaData(),
            Column("user_id", BigInteger, nullable=False, comment="User ID"),
            Column("event_date", Date, nullable=False, comment="Event date"),
            Column("city", String(50), server_default="Unknown"),
            Column("revenue", DOUBLE, server_default="0.0"),
            starrocks_engine="OLAP",
            starrocks_aggregate_key="user_id, event_date",
            comment="A comprehensive table",
            starrocks_partition_by=(
                'RANGE(event_date) (START ("2023-01-01") END ("2024-01-01") EVERY (INTERVAL 1 MONTH))'
            ),
            starrocks_distributed_by="HASH(user_id) BUCKETS 32",
            starrocks_order_by="event_date, user_id",
            starrocks_properties={
                "replication_num": "1",
                "storage_medium": "SSD",
                "bloom_filter_columns": "city",
                "dynamic_partition.enable": "true",
                "dynamic_partition.time_unit": "MONTH",
                "dynamic_partition.end": "3",
                "dynamic_partition.prefix": "p",
            },
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE comprehensive_table ("
            "    user_id BIGINT NOT NULL COMMENT 'User ID', "
            "    event_date DATE NOT NULL COMMENT 'Event date', "
            "    city VARCHAR(50) DEFAULT 'Unknown', "
            "    revenue DOUBLE DEFAULT '0.0'"
            ")"
            "AGGREGATE KEY (user_id, event_date)"
            "COMMENT 'A comprehensive table'"
            'PARTITION BY RANGE(event_date) (START ("2023-01-01") END ("2024-01-01") EVERY (INTERVAL 1 MONTH))'
            "DISTRIBUTED BY HASH(user_id) BUCKETS 32"
            "ORDER BY (event_date, user_id)"
            "PROPERTIES("
            '    "replication_num"="1",'
            '    "storage_medium"="SSD",'
            '    "bloom_filter_columns"="city",'
            '    "dynamic_partition.enable"="true",'
            '    "dynamic_partition.time_unit"="MONTH",'
            '    "dynamic_partition.end"="3",'
            '    "dynamic_partition.prefix"="p"'
            ")",
        )


class CreateIndexCompileTest(fixtures.TestBase, AssertsCompiledSQL):
    """``CREATE INDEX`` follows StarRocks' grammar: plain columns, and the index type as an unquoted keyword."""

    __dialect__ = StarRocksDialect()

    @staticmethod
    def _table():
        return Table("events", MetaData(), Column("id", Integer), Column("city", String(20)), Column("tags", String(50)))

    @testing.combinations(
        ("no_type", {}, "CREATE INDEX ix_city ON events (city)"),
        ("bitmap", {"starrocks_using": "BITMAP"}, "CREATE INDEX ix_city ON events (city) USING BITMAP"),
        ("lower_case_type", {"starrocks_using": "ngrambf"}, "CREATE INDEX ix_city ON events (city) USING NGRAMBF"),
        ("gin", {"starrocks_using": "GIN"}, "CREATE INDEX ix_city ON events (city) USING GIN"),
        argnames="kwargs, expected",
        id_="iaa",
    )
    def test_index_type(self, kwargs, expected):
        table = self._table()
        self.assert_compile(CreateIndex(Index("ix_city", table.c.city, **kwargs)), expected)

    @testing.combinations(
        (
            "properties",
            {"starrocks_using": "NGRAMBF", "starrocks_properties": {"gram_num": "4", "bloom_filter_fpp": "0.05"}},
            'CREATE INDEX ix_city ON events (city) USING NGRAMBF ("gram_num" = "4", "bloom_filter_fpp" = "0.05")',
        ),
        (
            "comment",
            {"starrocks_using": "BITMAP", "starrocks_comment": "city's index"},
            "CREATE INDEX ix_city ON events (city) USING BITMAP COMMENT 'city''s index'",
        ),
        (
            "comment_without_type",
            {"starrocks_comment": "plain"},
            "CREATE INDEX ix_city ON events (city) COMMENT 'plain'",
        ),
        argnames="kwargs, expected",
        id_="iaa",
    )
    def test_properties_and_comment(self, kwargs, expected):
        table = self._table()
        self.assert_compile(CreateIndex(Index("ix_city", table.c.city, **kwargs)), expected)

    def test_properties_without_type_raise(self):
        table = self._table()
        with expect_raises_message(exc.CompileError, "has starrocks_properties but no starrocks_using"):
            CreateIndex(Index("ix_city", table.c.city, starrocks_properties={"gram_num": "4"})).compile(
                dialect=self.__dialect__
            )

    def test_several_columns_are_refused(self):
        """StarRocks answers "index can only apply to a single column"."""
        table = self._table()
        with expect_raises_message(exc.CompileError, "covers one column"):
            CreateIndex(Index("ix_city_tags", table.c.city, table.c.tags)).compile(dialect=self.__dialect__)

    def test_drop_index(self):
        table = self._table()
        self.assert_compile(DropIndex(Index("ix_city", table.c.city)), "DROP INDEX ix_city ON events")

    def test_drop_index_if_exists_is_refused(self):
        table = self._table()
        with expect_raises_message(exc.CompileError, "no DROP INDEX IF EXISTS"):
            DropIndex(Index("ix_city", table.c.city), if_exists=True).compile(dialect=self.__dialect__)

    def test_unknown_index_type_raises(self):
        table = self._table()
        with expect_raises_message(exc.CompileError, "Unknown StarRocks index type 'BTREE'"):
            CreateIndex(Index("ix_city", table.c.city, starrocks_using="BTREE")).compile(dialect=self.__dialect__)

    def test_unique_index_raises(self):
        table = self._table()
        with expect_raises_message(exc.CompileError, "StarRocks has no unique indexes"):
            CreateIndex(Index("ix_city", table.c.city, unique=True)).compile(dialect=self.__dialect__)

    def test_if_not_exists_raises(self):
        table = self._table()
        with expect_raises_message(exc.CompileError, "StarRocks has no CREATE INDEX IF NOT EXISTS"):
            CreateIndex(Index("ix_city", table.c.city), if_not_exists=True).compile(dialect=self.__dialect__)

    def test_expression_index_raises(self):
        table = self._table()
        with expect_raises_message(exc.CompileError, "StarRocks indexes plain columns only"):
            CreateIndex(Index("ix_lower_city", func.lower(table.c.city))).compile(dialect=self.__dialect__)


class CreateTablePropertyQuotingTest(fixtures.TestBase, AssertsCompiledSQL):
    """PROPERTIES values are quoted so that StarRocks, which unescapes them once, stores them unchanged."""

    __dialect__ = StarRocksDialect()

    @testing.combinations(
        ("double_quote", 'grp"1', r'"grp\"1"'),
        ("backslash", r"C:\temp", r'"C:\\temp"'),
        ("escaped_quote", r"a\"b", r'"a\\\"b"'),
        argnames="value, rendered_value",
        id_="iaa",
    )
    def test_property_value_quoting(self, value, rendered_value):
        tbl = Table(
            "quoted_props",
            MetaData(),
            Column("id", Integer),
            starrocks_duplicate_key="id",
            starrocks_properties={"colocate_with": value},
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE quoted_props ("
            "    id INTEGER"
            ")"
            "DUPLICATE KEY (id)"
            "PROPERTIES("
            f'    "colocate_with"={rendered_value}'
            ")",
        )


class InferredPrimaryKeyCompileTest(fixtures.TestBase, AssertsCompiledSQL):
    """A table with ``primary_key=True`` columns and no ``starrocks_*_key`` is created as a PRIMARY KEY table."""

    __dialect__ = StarRocksDialect()

    def test_single_column(self):
        tbl = Table("pk_inferred", MetaData(), Column("id", Integer, primary_key=True), Column("v", String(10)))
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE pk_inferred ("
            "    id INTEGER NOT NULL, "
            "    v VARCHAR(10)"
            ")"
            "PRIMARY KEY (id)",
        )

    def test_composite_key_in_column_order(self):
        tbl = Table(
            "pk_composite",
            MetaData(),
            Column("tenant", Integer, primary_key=True),
            Column("id", Integer, primary_key=True),
            Column("v", String(10)),
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE pk_composite ("
            "    tenant INTEGER NOT NULL, "
            "    id INTEGER NOT NULL, "
            "    v VARCHAR(10)"
            ")"
            "PRIMARY KEY (tenant, id)",
        )

    def test_key_columns_not_first_raise(self):
        tbl = Table("pk_late", MetaData(), Column("v", String(10)), Column("id", Integer, primary_key=True))
        with expect_raises_message(exc.CompileError, "needs a PRIMARY KEY table's key columns to come first"):
            CreateTable(tbl).compile(dialect=self.__dialect__)

    def test_named_key_model_wins(self):
        tbl = Table(
            "pk_duplicate",
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("v", String(10)),
            starrocks_duplicate_key="id",
        )
        self.assert_compile(
            CreateTable(tbl),
            "CREATE TABLE pk_duplicate ("
            "    id INTEGER NOT NULL, "
            "    v VARCHAR(10)"
            ")"
            "DUPLICATE KEY (id)",
        )

    def test_no_primary_key_columns_leave_the_key_to_starrocks(self):
        tbl = Table("no_pk", MetaData(), Column("id", Integer), Column("v", String(10)))
        self.assert_compile(CreateTable(tbl), "CREATE TABLE no_pk (" "    id INTEGER, " "    v VARCHAR(10)" ")")

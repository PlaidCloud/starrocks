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

"""Transactions: connections autocommit by default, and ``READ COMMITTED`` runs DML in StarRocks transactions."""

from sqlalchemy import MetaData, Table, delete, exc, insert, literal, select, testing, update
from sqlalchemy.testing import config, engines, fixtures
from sqlalchemy.testing.assertions import eq_, expect_raises, expect_raises_message
from sqlalchemy.testing.schema import Column

from starrocks import INTEGER


TRANSACTIONS_NEED_3_5 = testing.skip_if("starrocks<3.5", "StarRocks has transactions from 3.5")
KEYED_DELETE_IN_TRANSACTION_NEEDS_4_1 = testing.skip_if(
    "starrocks<4.1",
    "StarRocks 3.5 refuses UPDATE and DELETE inside a transaction, and documents them for shared-data clusters "
    "from 4.0; 4.1 shared-nothing allows them too",
)


class _RollBack(Exception):
    """Raised inside a transaction block to roll it back."""


class TransactionTest(fixtures.TablesTest):
    __backend__ = True
    __only_on__ = "starrocks"

    run_deletes = "each"

    @classmethod
    def define_tables(cls, metadata):
        Table("txn_keyed", metadata, Column("id", INTEGER, primary_key=True, autoincrement=False), Column("v", INTEGER))
        Table("txn_logged", metadata, Column("id", INTEGER), Column("v", INTEGER), starrocks_duplicate_key="id")
        Table(
            "txn_translated",
            metadata,
            Column("id", INTEGER),
            Column("v", INTEGER),
            starrocks_duplicate_key="id",
            schema=config.test_schema,
        )

    @staticmethod
    def _read_committed_engine():
        return engines.testing_engine(options={"isolation_level": "READ COMMITTED"})

    def _ids(self, table_name):
        with config.db.connect() as conn:
            return sorted(row.id for row in conn.execute(select(self.tables[table_name].c.id)))

    def _insert_logged_rows(self):
        with config.db.begin() as conn:
            conn.execute(insert(self.tables.txn_logged), [{"id": 1, "v": 1}, {"id": 2, "v": 2}])

    def test_connections_autocommit_by_default(self):
        with config.db.connect() as conn:
            eq_(conn.get_isolation_level(), "AUTOCOMMIT")
        eq_(config.db.dialect.default_isolation_level, "AUTOCOMMIT")

    def test_rollback_under_autocommit_keeps_the_rows(self):
        with expect_raises(_RollBack):
            with config.db.begin() as conn:
                conn.execute(insert(self.tables.txn_keyed), {"id": 1, "v": 1})
                raise _RollBack()
        eq_(self._ids("txn_keyed"), [1])

    def test_read_committed_execution_option_is_reset_when_the_connection_returns(self):
        engine = engines.testing_engine()
        with engine.connect() as conn:
            conn.execution_options(isolation_level="READ COMMITTED")
            eq_(conn.get_isolation_level(), "READ COMMITTED")
            dbapi_connection = conn.connection.dbapi_connection
        with engine.connect() as conn:
            assert conn.connection.dbapi_connection is dbapi_connection
            eq_(conn.get_isolation_level(), "AUTOCOMMIT")

    def test_other_isolation_levels_are_refused(self):
        with expect_raises_message(exc.ArgumentError, "Invalid value 'SERIALIZABLE' for isolation_level"):
            with engines.testing_engine(options={"isolation_level": "SERIALIZABLE"}).connect():
                pass

    @TRANSACTIONS_NEED_3_5
    def test_read_committed_rolls_back_the_inserts_of_several_tables(self):
        with expect_raises(_RollBack):
            with self._read_committed_engine().begin() as conn:
                conn.execute(insert(self.tables.txn_keyed), {"id": 1, "v": 1})
                conn.execute(insert(self.tables.txn_logged), {"id": 1, "v": 1})
                raise _RollBack()
        eq_(self._ids("txn_keyed"), [])
        eq_(self._ids("txn_logged"), [])

    @TRANSACTIONS_NEED_3_5
    def test_read_committed_commits_the_inserts_of_several_tables(self):
        with self._read_committed_engine().begin() as conn:
            conn.execute(insert(self.tables.txn_keyed), [{"id": 1, "v": 1}, {"id": 2, "v": 2}])
            conn.execute(insert(self.tables.txn_logged), {"id": 1, "v": 1})
        eq_(self._ids("txn_keyed"), [1, 2])
        eq_(self._ids("txn_logged"), [1])

    @TRANSACTIONS_NEED_3_5
    def test_uncommitted_rows_are_invisible_to_other_connections(self):
        with self._read_committed_engine().begin() as conn:
            conn.execute(insert(self.tables.txn_keyed), {"id": 1, "v": 1})
            eq_(self._ids("txn_keyed"), [])
        eq_(self._ids("txn_keyed"), [1])

    @TRANSACTIONS_NEED_3_5
    def test_a_transaction_that_only_reads_opens_none(self):
        with self._read_committed_engine().connect() as conn:
            with conn.begin():
                conn.execute(select(self.tables.txn_keyed))
                assert not conn.connection.info.get("starrocks_transaction_open")
                conn.execute(insert(self.tables.txn_keyed), {"id": 1, "v": 1})
                assert conn.connection.info.get("starrocks_transaction_open")
            assert not conn.connection.info.get("starrocks_transaction_open")

    @TRANSACTIONS_NEED_3_5
    def test_ddl_commits_the_open_transaction(self):
        created = Table("txn_created", MetaData(), Column("id", INTEGER, primary_key=True, autoincrement=False))
        try:
            with expect_raises(_RollBack):
                with self._read_committed_engine().begin() as conn:
                    conn.execute(insert(self.tables.txn_keyed), {"id": 1, "v": 1})
                    created.create(conn)
                    conn.execute(insert(self.tables.txn_logged), {"id": 1, "v": 1})
                    raise _RollBack()
            eq_(self._ids("txn_keyed"), [1])
            eq_(self._ids("txn_logged"), [])
        finally:
            created.drop(config.db, checkfirst=True)

    @TRANSACTIONS_NEED_3_5
    def test_a_transaction_begun_as_text_is_ended_by_the_rollback(self):
        with config.db.connect() as conn:
            transaction = conn.begin()
            conn.exec_driver_sql("BEGIN WORK")
            conn.execute(insert(self.tables.txn_keyed), {"id": 1, "v": 1})
            transaction.rollback()
        eq_(self._ids("txn_keyed"), [])

    @TRANSACTIONS_NEED_3_5
    @testing.combinations(
        ("construct",), ("hinted_construct",), ("text",), ("qualified_text",), ("hinted_text",), ("catalog_text",),
        ("hint_without_a_space",), ("line_comment",), ("cte",),
        argnames="form",
    )
    def test_delete_on_a_duplicate_key_table_is_refused(self, form):
        self._insert_logged_rows()
        logged = self.tables.txn_logged
        database = config.db.url.database
        texts = {
            "text": "DELETE FROM txn_logged WHERE id = 2",
            "qualified_text": f"DELETE FROM `{database}`.`txn_logged` WHERE id = 2",
            "hinted_text": "DELETE /*+ SET_VAR(query_timeout=10) */ FROM txn_logged WHERE id = 2",
            "catalog_text": f"DELETE FROM default_catalog.`{database}`.txn_logged WHERE id = 2",
            "hint_without_a_space": "DELETE/*+ SET_VAR(query_timeout=10) */ FROM txn_logged WHERE id = 2",
            "line_comment": "DELETE -- purge\nFROM txn_logged WHERE id = 2",
            "cte": "WITH c AS (SELECT 2 AS id) DELETE FROM txn_logged WHERE id IN (SELECT id FROM c)",
        }
        with expect_raises_message(exc.InvalidRequestError, "at once, outside the transaction"):
            with self._read_committed_engine().begin() as conn:
                if form == "construct":
                    conn.execute(delete(logged).where(logged.c.id == 2))
                elif form == "hinted_construct":
                    conn.execute(delete(logged).where(logged.c.id == 2).prefix_with("/*+ SET_VAR(query_timeout=10) */"))
                else:
                    conn.exec_driver_sql(texts[form])
        eq_(self._ids("txn_logged"), [1, 2])

    @TRANSACTIONS_NEED_3_5
    def test_a_delete_through_a_schema_translate_map_is_refused(self):
        with config.db.begin() as conn:
            conn.execute(insert(self.tables[f"{config.test_schema}.txn_translated"]), {"id": 1, "v": 1})
        untranslated = Table("txn_translated", MetaData(), Column("id", INTEGER), Column("v", INTEGER))
        engine = self._read_committed_engine().execution_options(schema_translate_map={None: config.test_schema})
        with expect_raises_message(exc.InvalidRequestError, "at once, outside the transaction"):
            with engine.begin() as conn:
                conn.execute(delete(untranslated).where(untranslated.c.id == 1))
        with config.db.connect() as conn:
            eq_(conn.execute(select(self.tables[f"{config.test_schema}.txn_translated"].c.id)).scalars().all(), [1])

    def test_delete_on_a_duplicate_key_table_under_autocommit_runs(self):
        self._insert_logged_rows()
        with config.db.begin() as conn:
            conn.execute(delete(self.tables.txn_logged).where(self.tables.txn_logged.c.id == 2))
        eq_(self._ids("txn_logged"), [1])

    @TRANSACTIONS_NEED_3_5
    @KEYED_DELETE_IN_TRANSACTION_NEEDS_4_1
    def test_delete_on_a_primary_key_table_rolls_back(self):
        with config.db.begin() as conn:
            conn.execute(insert(self.tables.txn_keyed), [{"id": 1, "v": 1}, {"id": 2, "v": 2}])
        keyed = self.tables.txn_keyed
        with expect_raises(_RollBack):
            with self._read_committed_engine().begin() as conn:
                conn.execute(delete(keyed).where(keyed.c.id == 2))
                raise _RollBack()
        eq_(self._ids("txn_keyed"), [1, 2])

    @TRANSACTIONS_NEED_3_5
    def test_read_committed_rolls_back_an_executemany(self):
        with expect_raises(_RollBack):
            with self._read_committed_engine().begin() as conn:
                conn.execute(insert(self.tables.txn_keyed), [{"id": i, "v": i} for i in range(1, 6)])
                raise _RollBack()
        eq_(self._ids("txn_keyed"), [])

    @TRANSACTIONS_NEED_3_5
    def test_commented_text_dml_is_transactional(self):
        with expect_raises(_RollBack):
            with self._read_committed_engine().begin() as conn:
                conn.exec_driver_sql("/* note */ INSERT INTO txn_keyed VALUES (1, 1)")
                raise _RollBack()
        eq_(self._ids("txn_keyed"), [])

    @TRANSACTIONS_NEED_3_5
    @KEYED_DELETE_IN_TRANSACTION_NEEDS_4_1
    def test_an_update_with_a_cte_is_transactional(self):
        keyed = self.tables.txn_keyed
        with config.db.begin() as conn:
            conn.execute(insert(keyed), {"id": 1, "v": 1})
        source = select(literal(100).label("v")).cte("source")
        with expect_raises(_RollBack):
            with self._read_committed_engine().begin() as conn:
                conn.execute(
                    update(keyed).where(keyed.c.id == 1).values(v=select(source.c.v).scalar_subquery()).add_cte(source)
                )
                raise _RollBack()
        with config.db.connect() as conn:
            eq_(conn.execute(select(keyed.c.v)).all(), [(1,)])

    @TRANSACTIONS_NEED_3_5
    def test_text_ddl_commits_the_open_transaction(self):
        try:
            with expect_raises(_RollBack):
                with self._read_committed_engine().begin() as conn:
                    conn.execute(insert(self.tables.txn_keyed), {"id": 1, "v": 1})
                    conn.exec_driver_sql(
                        "CREATE TABLE txn_text_created (id INT) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                        "PROPERTIES ('replication_num' = '1')"
                    )
                    raise _RollBack()
            eq_(self._ids("txn_keyed"), [1])
        finally:
            with config.db.begin() as conn:
                conn.exec_driver_sql("DROP TABLE IF EXISTS txn_text_created")

    @TRANSACTIONS_NEED_3_5
    @testing.combinations(
        ("read_first", "SELECT 1; INSERT INTO txn_keyed VALUES (5, 5)"),
        ("write_first", "INSERT INTO txn_keyed VALUES (5, 5); INSERT INTO txn_logged VALUES (5, 5)"),
        argnames="statements",
        id_="ia",
    )
    def test_text_holding_several_statements_is_refused_under_read_committed(self, statements):
        with expect_raises_message(exc.InvalidRequestError, "Text holding several statements"):
            with self._read_committed_engine().begin() as conn:
                conn.exec_driver_sql(statements)
        eq_(self._ids("txn_keyed"), [])

    def test_text_holding_several_statements_runs_under_autocommit(self):
        with config.db.begin() as conn:
            conn.exec_driver_sql("INSERT INTO txn_keyed VALUES (1, 1); INSERT INTO txn_keyed VALUES (2, 2)")
        eq_(self._ids("txn_keyed"), [1, 2])

    @TRANSACTIONS_NEED_3_5
    def test_a_text_commit_ends_the_transaction_and_the_next_dml_opens_another(self):
        keyed = self.tables.txn_keyed
        with expect_raises(_RollBack):
            with self._read_committed_engine().begin() as conn:
                conn.execute(insert(keyed), {"id": 1, "v": 1})
                conn.exec_driver_sql("COMMIT")
                conn.execute(insert(keyed), {"id": 2, "v": 2})
                raise _RollBack()
        eq_(self._ids("txn_keyed"), [1])

    @TRANSACTIONS_NEED_3_5
    def test_a_delete_without_where_is_refused(self):
        self._insert_logged_rows()
        with expect_raises_message(exc.InvalidRequestError, "compiles to TRUNCATE TABLE"):
            with self._read_committed_engine().begin() as conn:
                conn.execute(delete(self.tables.txn_logged))
        eq_(self._ids("txn_logged"), [1, 2])

    @TRANSACTIONS_NEED_3_5
    def test_a_raw_cursor_transaction_is_rolled_back_when_the_connection_returns(self):
        engine = engines.testing_engine(options={"pool_size": 1, "max_overflow": 0})
        with engine.connect() as conn:
            cursor = conn.connection.cursor()
            cursor.execute("BEGIN WORK")
            cursor.execute("INSERT INTO txn_keyed VALUES (1, 1)")
        with engine.begin() as conn:
            conn.execute(insert(self.tables.txn_keyed), {"id": 2, "v": 2})
        eq_(self._ids("txn_keyed"), [2])

    @testing.only_if("starrocks<3.5", "StarRocks has transactions from 3.5")
    def test_a_text_begin_is_refused_before_3_5(self):
        with config.db.connect() as conn:
            with expect_raises_message(exc.InvalidRequestError, "needs StarRocks 3.5 or later"):
                conn.exec_driver_sql("BEGIN WORK")

    @testing.only_if("starrocks<3.5", "StarRocks has transactions from 3.5")
    def test_read_committed_dml_is_refused_before_3_5(self):
        with expect_raises_message(exc.InvalidRequestError, "needs StarRocks 3.5 or later"):
            with self._read_committed_engine().begin() as conn:
                conn.execute(insert(self.tables.txn_keyed), {"id": 1, "v": 1})
        eq_(self._ids("txn_keyed"), [])

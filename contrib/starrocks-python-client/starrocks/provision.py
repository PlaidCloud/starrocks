# Copyright 2021-present StarRocks, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https:#www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import time
from typing import Any

from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import sort_tables_and_constraints
from sqlalchemy.testing.provision import (
    configure_follower,
    create_db,
    drop_all_schema_objects_pre_tables,
    drop_db,
    temp_table_keyword_args,
)

from starrocks.dialect import StarRocksDialect


SCHEMA_CHANGE_TERMINAL_STATES = ("FINISHED", "CANCELLED")
SCHEMA_CHANGE_WAIT_SECONDS = 300.0
SCHEMA_CHANGE_POLL_SECONDS = 0.5

# from sqlalchemy import exc
# from sqlalchemy.testing.provision import generate_driver_url
#
#
# @generate_driver_url.for_db("starrocks")
# def generate_driver_url(url, driver, query_str):
#     backend = url.get_backend_name()
#
#     # NOTE: at the moment, tests are running mariadbconnector
#     # against both mariadb and mysql backends.   if we want this to be
#     # limited, do the decision making here to reject a "mysql+mariadbconnector"
#     # URL.  Optionally also re-enable the module level
#     # MySQLDialect_mariadbconnector.is_mysql flag as well, which must include
#     # a unit and/or functional test.
#
#     # all the Jenkins tests have been running mysqlclient Python library
#     # built against mariadb client drivers for years against all MySQL /
#     # MariaDB versions going back to MySQL 5.6, currently they can talk
#     # to MySQL databases without problems.
#
#     if backend == "starrocks":
#         dialect_cls = url.get_dialect()
#         if dialect_cls._is_mariadb_from_url(url):
#             backend = "mariadb"
#
#     new_url = url.set(
#         drivername="%s+%s" % (backend, driver)
#     ).update_query_string(query_str)
#
#     try:
#         new_url.get_dialect()
#     except exc.NoSuchModuleError:
#         return None
#     else:
#         return new_url
#

@create_db.for_db("starrocks")
def _mysql_create_db(cfg, eng, ident):
    with eng.begin() as conn:
        try:
            _starrocks_drop_db(cfg, conn, ident)
        except Exception:
            pass

    with eng.begin() as conn:
        conn.exec_driver_sql(
            "CREATE DATABASE %s" % ident
        )
        conn.exec_driver_sql(
            "CREATE DATABASE %s_test_schema" % ident
        )
        conn.exec_driver_sql(
            "CREATE DATABASE %s_test_schema_2" % ident
        )


@configure_follower.for_db("starrocks")
def _starrocks_configure_follower(config, ident):
    config.test_schema = "%s_test_schema" % ident
    config.test_schema_2 = "%s_test_schema_2" % ident


@drop_db.for_db("starrocks")
def _starrocks_drop_db(cfg, eng, ident):
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP DATABASE %s_test_schema" % ident)
        conn.exec_driver_sql("DROP DATABASE %s_test_schema_2" % ident)
        conn.exec_driver_sql("DROP DATABASE %s" % ident)

@drop_all_schema_objects_pre_tables.for_db("starrocks")
def _starrocks_drop_materialized_views(cfg: Any, eng: Engine) -> None:
    """Drop the materialized views earlier runs left, which ``--dropfirst`` cleans up along with tables and views.

    SQLAlchemy's own cleanup drops materialized views only for a dialect whose ``materialized_views`` requirement
    is open, and with DROP VIEW, which StarRocks refuses for one.
    """
    inspector = inspect(eng)
    preparer = eng.dialect.identifier_preparer
    with eng.begin() as conn:
        for schema in (None, cfg.test_schema, cfg.test_schema_2):
            for name in inspector.get_materialized_view_names(schema=schema):
                qualified_name = ".".join(preparer.quote(part) for part in (schema, name) if part)
                conn.exec_driver_sql(f"DROP MATERIALIZED VIEW IF EXISTS {qualified_name}")


@temp_table_keyword_args.for_db("starrocks")
def _starrocks_temp_table_keyword_args(cfg, eng):
    return {"prefixes": ["TEMPORARY"]}


def wait_for_schema_changes(connection: Connection, table_name: str, schema: str | None = None) -> None:
    """Block until the newest ``SHOW ALTER TABLE COLUMN`` job on the table reaches a terminal state.

    Index and column changes run asynchronously in StarRocks, and the table rejects further DDL
    or TRUNCATE ("A schema change operation is in progress") until the job finishes.

    Raises:
        TimeoutError: if the job is still running after ``SCHEMA_CHANGE_WAIT_SECONDS``.
    """
    newest_job = text(
        StarRocksDialect.gen_show_alter_table_statement(
            table_name, "COLUMN", schema=schema, state=None, order_by="JobId DESC", limit=1, bind_table_name=True
        )
    )
    deadline = time.monotonic() + SCHEMA_CHANGE_WAIT_SECONDS
    while True:
        job = connection.execute(newest_job, {"table_name": table_name}).mappings().first()
        if job is None or job.get("State") in SCHEMA_CHANGE_TERMINAL_STATES:
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"schema change on {table_name} still {job.get('State')} after {SCHEMA_CHANGE_WAIT_SECONDS}s")
        time.sleep(SCHEMA_CHANGE_POLL_SECONDS)


def _existing_tables_in_drop_order(connection: Connection, metadata: MetaData) -> list[Table]:
    inspector = inspect(connection)
    ordered = [table for table, _ in sort_tables_and_constraints(metadata.tables.values()) if table is not None]
    return [table for table in reversed(ordered) if table.name in inspector.get_table_names(schema=table.schema)]


try:
    from sqlalchemy.testing.provision import delete_from_all_tables
except ImportError:
    delete_from_all_tables = None

if delete_from_all_tables is not None:

    @delete_from_all_tables.for_db("starrocks")
    def _starrocks_delete_from_all_tables(connection: Connection, cfg: object, metadata: MetaData) -> None:
        """Empty the test tables, letting index builds from table setup finish first.

        SQLAlchemy 1.4 has no such hook; it empties tables itself and only prints failures.
        """
        for table in _existing_tables_in_drop_order(connection, metadata):
            wait_for_schema_changes(connection, table.name, table.schema)
            connection.execute(table.delete())

# Uncomment to debug SQL Statements in tests
# from sqlalchemy.testing.provision import update_db_opts
# @update_db_opts.for_db("starrocks")
# def _starrocks_update_db_opts(db_url, db_opts):
#     db_opts["echo"] = True

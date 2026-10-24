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

"""How the dialect maps isolation levels onto the driver's autocommit, and which DELETE target it reads."""

from types import SimpleNamespace

from sqlalchemy import exc, testing
from sqlalchemy.engine import make_url
from sqlalchemy.testing import fixtures
from sqlalchemy.testing.assertions import eq_, expect_raises_message

from starrocks.dialect import StarRocksDialect


class _DriverConnection:
    def __init__(self, autocommit_mode):
        self.autocommit_mode = autocommit_mode
        self.commits = 0
        self.rollbacks = 0

    def autocommit(self, value):
        self.autocommit_mode = bool(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class IsolationLevelTest(fixtures.TestBase):
    @testing.combinations(
        ("default", "", True),
        ("off", "?autocommit=false", False),
        ("on", "?autocommit=true", True),
        argnames="query,autocommit",
        id_="iaa",
    )
    def test_connect_args_turn_autocommit_on_unless_the_url_turns_it_off(self, query, autocommit):
        _, connect_args = StarRocksDialect().create_connect_args(make_url(f"starrocks://root@host:9030/db{query}"))
        eq_(connect_args["autocommit"], autocommit)

    def test_levels(self):
        eq_(StarRocksDialect().get_isolation_level_values(None), ("AUTOCOMMIT", "READ COMMITTED"))

    @testing.combinations(
        ("READ COMMITTED", False, "READ COMMITTED"),
        ("read_committed", False, "READ COMMITTED"),
        ("AUTOCOMMIT", True, "AUTOCOMMIT"),
        argnames="level,autocommit_mode,reported",
    )
    def test_level_sets_the_driver_autocommit(self, level, autocommit_mode, reported):
        dialect = StarRocksDialect()
        connection = _DriverConnection(not autocommit_mode)
        dialect.set_isolation_level(connection, level)
        eq_(connection.autocommit_mode, autocommit_mode)
        eq_(dialect.get_isolation_level(connection), reported)

    def test_a_pooled_connection_is_unwrapped(self):
        dialect = StarRocksDialect()
        connection = _DriverConnection(True)
        dialect.set_isolation_level(SimpleNamespace(dbapi_connection=connection), "READ COMMITTED")
        eq_(connection.autocommit_mode, False)

    def test_the_server_default_autocommits(self):
        eq_(StarRocksDialect().get_isolation_level(_DriverConnection(None)), "AUTOCOMMIT")

    @testing.combinations(("SERIALIZABLE",), ("REPEATABLE READ",), ("READ UNCOMMITTED",), argnames="level")
    def test_other_levels_are_refused(self, level):
        with expect_raises_message(exc.ArgumentError, f"Invalid value '{level}' for isolation_level"):
            StarRocksDialect().set_isolation_level(_DriverConnection(True), level)


class TransactionEndTest(fixtures.TestBase):
    @staticmethod
    def _pooled(open_transaction):
        info = {"starrocks_transaction_open": True} if open_transaction else {}
        driver = _DriverConnection(False)
        return SimpleNamespace(info=info, commit=driver.commit, rollback=driver.rollback), driver

    @testing.combinations((True, 1), (False, 0), argnames="open_transaction,sent")
    def test_commit_and_rollback_go_to_the_server_only_for_an_open_transaction(self, open_transaction, sent):
        dialect = StarRocksDialect()
        pooled, driver = self._pooled(open_transaction)
        dialect.do_commit(pooled)
        pooled.info["starrocks_transaction_open"] = open_transaction
        dialect.do_rollback(pooled)
        eq_((driver.commits, driver.rollbacks), (sent, sent))
        eq_(pooled.info, {})

    def test_an_untracked_connection_is_committed(self):
        driver = _DriverConnection(False)
        StarRocksDialect().do_commit(driver)
        StarRocksDialect().do_rollback(driver)
        eq_((driver.commits, driver.rollbacks), (1, 1))


class DeleteTargetTest(fixtures.TestBase):
    @testing.combinations(
        ("DELETE FROM t WHERE id = 1", ("t", None)),
        ("delete  from `s`.`t` where 1", ("t", "s")),
        ("DELETE FROM `a``b` WHERE x = 1", ("a`b", None)),
        ("DELETE FROM s . t USING u WHERE t.id = u.id", ("t", "s")),
        ("\n  DELETE FROM `my-db`.events", ("events", "my-db")),
        ("SELECT * FROM t", None),
        ("INSERT INTO t VALUES (1)", None),
        argnames="statement,target",
    )
    def test_target(self, statement, target):
        eq_(StarRocksDialect._delete_target(statement), target)

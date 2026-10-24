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

from typing import Any, Dict

from alembic.testing.requirements import SuiteRequirements as AlembicSuiteRequirements
import sqlalchemy
from sqlalchemy.testing import exclusions
from sqlalchemy.testing.requirements import SuiteRequirements


IS_SQLALCHEMY_2 = int(sqlalchemy.__version__.split(".")[0]) >= 2


class Requirements(SuiteRequirements, AlembicSuiteRequirements):
    @property
    def sqlalchemy_2(self) -> exclusions.compound:
        """SQLAlchemy 2.0 or greater, which the view, materialized view and Alembic features rely on"""
        return exclusions.skip_if(lambda config: not IS_SQLALCHEMY_2, "SQLAlchemy 2.0 or greater required")

    @property
    def unbounded_varchar(self) -> exclusions.compound:
        """``String()`` without a length compiles as STRING."""
        return exclusions.open()

    @property
    def temp_table_reflection(self) -> exclusions.compound:
        return exclusions.closed()

    @property
    def temp_table_reflect_indexes(self) -> exclusions.compound:
        return self.temp_table_reflection

    @property
    def temp_table_names(self) -> exclusions.compound:
        """target dialect supports listing of temporary table names"""
        return exclusions.closed()

    @property
    def has_temp_table(self) -> exclusions.compound:
        """target dialect supports checking a single temp table name"""
        return exclusions.closed()

    @property
    def temporary_tables(self) -> exclusions.compound:
        """target database supports temporary tables"""
        return exclusions.open()

    @property
    def temporary_views(self) -> exclusions.compound:
        """target database supports temporary views"""
        return exclusions.closed()

    @property
    def index_reflection(self) -> exclusions.compound:
        """Closed: the suite's fixture tables carry multi-column and unique indexes, which StarRocks does not have.

        StarRocks' single-column BITMAP, NGRAMBF, GIN and VECTOR indexes are reflected, and tested in
        ``test/integration/test_reflection_indexes.py``.
        """
        return exclusions.closed()

    @property
    def primary_key_constraint_reflection(self) -> exclusions.compound:
        """PRIMARY KEY, UNIQUE KEY and AGGREGATE KEY tables reflect their key as the primary key."""
        return exclusions.open()

    @property
    def foreign_key_constraint_reflection(self) -> exclusions.compound:
        return exclusions.closed()

    @property
    def foreign_key_name_reflection(self) -> exclusions.compound:
        """Alembic: FK names reflect; StarRocks reflects no foreign keys at all"""
        return exclusions.closed()

    @property
    def fk_names(self) -> exclusions.compound:
        """Alembic: FK names reflect (Alembic before 1.20 leaves this open); StarRocks has no foreign keys"""
        return exclusions.closed()

    @property
    def fk_onupdate(self) -> exclusions.compound:
        """Alembic: ON UPDATE round-trips through autogenerate; StarRocks has no foreign keys"""
        return exclusions.closed()

    @property
    def fk_ondelete_restrict(self) -> exclusions.compound:
        """Alembic: ON DELETE RESTRICT round-trips through autogenerate; StarRocks has no foreign keys"""
        return exclusions.closed()

    @property
    def fk_onupdate_restrict(self) -> exclusions.compound:
        """Alembic: ON UPDATE RESTRICT round-trips through autogenerate; StarRocks has no foreign keys"""
        return exclusions.closed()

    @property
    def fk_ondelete_noaction(self) -> exclusions.compound:
        """Alembic: ON DELETE NO ACTION round-trips through autogenerate; StarRocks has no foreign keys"""
        return exclusions.closed()

    @property
    def unique_constraint_reflection(self) -> exclusions.compound:
        """target dialect supports reflection of unique constraints"""
        return exclusions.closed()

    @property
    def binary_literals(self) -> exclusions.compound:
        """``CAST('foo' AS BINARY)`` works, and binary values round-trip."""
        return exclusions.open()

    @property
    def datetime_implicit_bound(self) -> exclusions.compound:
        """target dialect when given a datetime object will bind it such
        that the database server knows the object is a datetime, and not
        a plain string.
        https://github.com/sqlalchemy/sqlalchemy/discussions/9661
        """
        return exclusions.open()

    @property
    def duplicate_key_raises_integrity_error(self) -> exclusions.compound:
        """target dialect raises IntegrityError when reporting an INSERT
        with a primary key violation.  (hint: it should)

        """
        return exclusions.closed()

    @property
    def offset(self) -> exclusions.compound:
        """OFFSET renders after a LIMIT, which the dialect adds when the query has none."""
        return exclusions.open()

    @property
    def bound_limit_offset(self) -> exclusions.compound:
        """LIMIT and OFFSET take bound parameters."""
        return exclusions.open()

    @property
    def sql_expression_limit_offset(self) -> exclusions.compound:
        """Closed: StarRocks takes only a number or a bound parameter in LIMIT and OFFSET, not an expression."""
        return exclusions.closed()

    @property
    def json_type(self) -> exclusions.compound:
        """target platform implements a native JSON type."""

        return exclusions.open()

    @property
    def time(self) -> exclusions.compound:
        """Closed: StarRocks has no TIME column type."""
        return exclusions.closed()

    @property
    def time_microseconds(self) -> exclusions.compound:
        """Closed: StarRocks has no TIME column type."""
        return exclusions.closed()

    @property
    def implements_get_lastrowid(self) -> exclusions.compound:
        """target dialect implements the executioncontext.get_lastrowid()
        method without reliance on RETURNING.
        """

        return exclusions.closed()

    @property
    def reflect_table_options(self) -> exclusions.compound:
        """Target database must support reflecting table_options."""

        return exclusions.open()

    @property
    def comment_reflection(self) -> exclusions.compound:
        """Table and column comments are reflected, on 3.3 as on later versions."""
        return exclusions.open()

    @property
    def sane_rowcount(self) -> exclusions.compound:
        """UPDATE and DELETE on a PRIMARY KEY table report how many rows they matched.

        A DELETE on a DUPLICATE or UNIQUE KEY table reports 0 rows, even when it deletes some, so the ORM warns that
        it matched none; the suite's tables are PRIMARY KEY tables.
        """
        return exclusions.open()

    @property
    def views(self) -> exclusions.compound:
        """Target database must support VIEWs."""

        return exclusions.open()

    @property
    def like_escapes(self) -> exclusions.compound:
        """Closed: StarRocks has no ``LIKE ... ESCAPE``."""
        return exclusions.closed()

    @property
    def legacy_unconditional_json_extract(self) -> exclusions.compound:
        """``json_query`` returns valid JSON in all cases, as the legacy ``JSON_EXTRACT`` does."""
        return exclusions.open()

    @property
    def update_from(self) -> exclusions.compound:
        """Target must support UPDATE..FROM syntax"""
        return exclusions.open()

    @property
    def delete_from(self) -> exclusions.compound:
        """Target must support DELETE FROM..FROM or DELETE..USING syntax"""
        return exclusions.open()

    @property
    def empty_inserts(self) -> exclusions.compound:
        return exclusions.closed()

    @property
    def precision_generic_float_type(self) -> exclusions.compound:
        """Closed: FLOAT is 32-bit, so 15.7563827 comes back as 15.7563830."""
        return exclusions.closed()

    @property
    def ctes(self) -> exclusions.compound:
        """Target database supports CTEs"""
        return exclusions.open()

    @property
    def ctes_with_update_delete(self) -> exclusions.compound:
        """target database supports CTES that ride on top of a normal UPDATE
        or DELETE statement which refers to the CTE in a correlated subquery.

        """
        return exclusions.open()

    @property
    def ctes_with_values(self) -> exclusions.compound:
        """target database supports CTES that ride on top of a VALUES
        clause."""
        return exclusions.closed()

    @property
    def ctes_on_dml(self) -> exclusions.compound:
        """Closed: a CTE holding INSERT, UPDATE or DELETE, ``WITH x AS (DELETE ...)``, is a syntax error."""
        return exclusions.closed()

    @property
    def enums(self) -> exclusions.compound:
        """target database supports ENUM type"""
        return exclusions.closed()

    @property
    def unicode_ddl(self) -> exclusions.compound:
        return exclusions.open()

    @property
    def window_functions(self) -> exclusions.compound:
        """``OVER (PARTITION BY ... ORDER BY ... ROWS BETWEEN ...)``"""
        return exclusions.open()

    @property
    def intersect(self) -> exclusions.compound:
        return exclusions.open()

    @property
    def except_(self) -> exclusions.compound:
        return exclusions.open()

    @property
    def nullsordering(self) -> exclusions.compound:
        """``ORDER BY ... NULLS FIRST`` and ``NULLS LAST``"""
        return exclusions.open()

    @property
    def table_value_constructor(self) -> exclusions.compound:
        """``SELECT * FROM (VALUES (...), (...)) AS t``"""
        return exclusions.open()

    @property
    def regexp_match(self) -> exclusions.compound:
        return exclusions.open()

    @property
    def regexp_replace(self) -> exclusions.compound:
        return exclusions.open()

    @property
    def supports_bitwise_and(self) -> exclusions.compound:
        return exclusions.open()

    @property
    def supports_bitwise_or(self) -> exclusions.compound:
        return exclusions.open()

    @property
    def supports_bitwise_xor(self) -> exclusions.compound:
        return exclusions.open()

    @property
    def supports_bitwise_not(self) -> exclusions.compound:
        """``~x``. The shift operators stay closed: StarRocks has ``bit_shift_left`` and ``bit_shift_right``, not ``<<`` and ``>>``."""
        return exclusions.open()

    @property
    def date_historic(self) -> exclusions.compound:
        """Dates from year 1 round-trip."""
        return exclusions.open()

    @property
    def datetime_historic(self) -> exclusions.compound:
        return exclusions.open()

    @property
    def datetime_literals(self) -> exclusions.compound:
        return exclusions.open()

    @property
    def isolation_level(self) -> exclusions.compound:
        """``AUTOCOMMIT``, the default, and ``READ COMMITTED``, which runs DML in StarRocks transactions."""
        return exclusions.open()

    @property
    def autocommit(self) -> exclusions.compound:
        return exclusions.open()

    def get_isolation_levels(self, config: Any) -> Dict[str, Any]:
        """The default level, and the levels the server can run: ``READ COMMITTED`` needs StarRocks 3.5."""
        with config.db.connect() as conn:
            supported = list(conn.dialect.get_isolation_level_values(None))
            if conn.dialect.server_version_info < (3, 5):
                supported = [level for level in supported if level == "AUTOCOMMIT"]
            return {"default": conn.dialect.default_isolation_level, "supported": supported}

    @property
    def nvarchar_types(self) -> exclusions.compound:
        """NCHAR and NVARCHAR, which compile to StarRocks' CHAR and VARCHAR, both UTF-8."""
        return exclusions.open()

    @property
    def table_ddl_if_exists(self) -> exclusions.compound:
        """``CREATE TABLE IF NOT EXISTS`` and ``DROP TABLE IF EXISTS``."""
        return exclusions.open()

    @property
    def comment_reflection_full_unicode(self) -> exclusions.compound:
        """Comments of any Unicode text reflect unchanged."""
        return exclusions.open()

    @property
    def schema_create_delete(self) -> exclusions.compound:
        """``CREATE SCHEMA`` and ``DROP SCHEMA``, which StarRocks takes as databases."""
        return exclusions.open()

    @property
    def precision_numerics_retains_significant_digits(self) -> exclusions.compound:
        """DECIMAL keeps trailing zeros: ``Numeric(5, 3)`` reads 1.000 back as ``Decimal('1.000')``.

        ``Numeric`` without a precision is ``decimal(10, 0)`` in StarRocks, so it rounds a fraction away.
        """
        return exclusions.open()

    @property
    def empty_inserts_executemany(self) -> exclusions.compound:
        """An executemany of an INSERT whose parameter sets are all empty inserts the column defaults."""
        return exclusions.open()

    @property
    def computed_columns(self) -> exclusions.compound:
        """Generated columns work, but StarRocks wants the column type to be the expression's type exactly.

        The suite's tables declare INTEGER columns computed as ``side * side``, which StarRocks types as BIGINT, and
        it rejects them with "Illegal expression type for Generated Column".
        """
        return exclusions.closed()

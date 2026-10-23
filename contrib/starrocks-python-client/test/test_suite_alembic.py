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

"""The Alembic dialect suite, with StarRocks limitations skipped and known dialect bugs marked as failing.

Tests are re-bound to the upstream function wrapped in an exclusion, so upstream combinations are kept.
"""

from alembic.testing import suite as alembic_suite
from alembic.testing.suite import *  # noqa: F403
from alembic.testing.suite import (
    AutogenerateCommentsTest as _AutogenerateCommentsTest,
    AutogenerateFKOptionsTest,
    AutoincrementTest as _AutoincrementTest,
    BackendAlterColumnTest as _BackendAlterColumnTest,
)
from sqlalchemy import testing
from sqlalchemy.testing.fixtures import TestBase


PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS = testing.fails_on(
    "starrocks",
    "autogenerate emits alter_table_properties for default.replication_num, comparing against a hard-coded "
    "default instead of the server's (https://github.com/StarRocks/starrocks/pull/80738)",
)
MODIFY_DEFAULT_DROPS_TYPE = testing.fails_on(
    "starrocks", "the dialect renders MODIFY COLUMN ... DEFAULT without the column type, which StarRocks requires"
)
RENAME_COMBINED_WITH_MODIFY = testing.fails_on(
    "starrocks", "the dialect combines MODIFY COLUMN with RENAME COLUMN, which StarRocks rejects as conflicting"
)
FLOAT_FIRST_COLUMN = testing.skip("starrocks", "StarRocks cannot use FLOAT as a table's first (key) column")
ONLY_COLUMN_IS_KEY = testing.skip(
    "starrocks", "the fixture table's only column is its key, which StarRocks cannot make nullable or retype"
)
UNQUOTED_DEFAULT = testing.skip("starrocks", "StarRocks accepts only quoted string literals as DEFAULT values")


AutogenerateFKOptionsTest.__requires__ = ("foreign_key_constraint_reflection",)  # upstream gates only some of its tests


class AutogenerateCommentsTest(_AutogenerateCommentsTest):
    test_add_column_comment = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(_AutogenerateCommentsTest.test_add_column_comment)
    test_add_table_comment = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(_AutogenerateCommentsTest.test_add_table_comment)
    test_alter_column_comment = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(
        _AutogenerateCommentsTest.test_alter_column_comment
    )
    test_alter_table_comment = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(_AutogenerateCommentsTest.test_alter_table_comment)
    test_existing_column_comment_no_change = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(
        _AutogenerateCommentsTest.test_existing_column_comment_no_change
    )
    test_existing_table_comment_no_change = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(
        _AutogenerateCommentsTest.test_existing_table_comment_no_change
    )
    test_remove_column_comment = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(
        _AutogenerateCommentsTest.test_remove_column_comment
    )
    test_remove_table_comment = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(
        _AutogenerateCommentsTest.test_remove_table_comment
    )


if hasattr(alembic_suite, "AutogenerateEmptyCommentsTest"):

    class AutogenerateEmptyCommentsTest(alembic_suite.AutogenerateEmptyCommentsTest):
        test_column_empty_comment_no_change = FLOAT_FIRST_COLUMN(
            alembic_suite.AutogenerateEmptyCommentsTest.test_column_empty_comment_no_change
        )
        test_column_empty_to_nonempty_comment = FLOAT_FIRST_COLUMN(
            alembic_suite.AutogenerateEmptyCommentsTest.test_column_empty_to_nonempty_comment
        )
        test_column_nonempty_to_empty_comment = FLOAT_FIRST_COLUMN(
            alembic_suite.AutogenerateEmptyCommentsTest.test_column_nonempty_to_empty_comment
        )
        test_table_empty_comment_no_change = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(
            alembic_suite.AutogenerateEmptyCommentsTest.test_table_empty_comment_no_change
        )
        test_table_empty_to_nonempty_comment = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(
            alembic_suite.AutogenerateEmptyCommentsTest.test_table_empty_to_nonempty_comment
        )
        test_table_nonempty_to_empty_comment = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(
            alembic_suite.AutogenerateEmptyCommentsTest.test_table_nonempty_to_empty_comment
        )


class AutoincrementTest(_AutoincrementTest):
    test_alter_column_autoincrement_pk_explicit_true = PROPERTY_ALTER_FROM_HARDCODED_DEFAULTS(
        _AutoincrementTest.test_alter_column_autoincrement_pk_explicit_true
    )


class BackendAlterColumnTest(_BackendAlterColumnTest):
    test_add_server_default_int = MODIFY_DEFAULT_DROPS_TYPE(_BackendAlterColumnTest.test_add_server_default_int)
    test_modify_server_default_int = UNQUOTED_DEFAULT(_BackendAlterColumnTest.test_modify_server_default_int)
    test_modify_non_nullable_to_nullable = ONLY_COLUMN_IS_KEY(
        _BackendAlterColumnTest.test_modify_non_nullable_to_nullable
    )
    test_modify_nullable_to_non = ONLY_COLUMN_IS_KEY(_BackendAlterColumnTest.test_modify_nullable_to_non)
    test_modify_type_int_str = ONLY_COLUMN_IS_KEY(_BackendAlterColumnTest.test_modify_type_int_str)
    test_rename_column = RENAME_COMBINED_WITH_MODIFY(_BackendAlterColumnTest.test_rename_column)


def _require_sqlalchemy_2(namespace: dict[str, object]) -> None:
    """Make every suite class require SQLAlchemy 2.0, which the dialect's Alembic integration depends on."""
    for value in list(namespace.values()):
        if isinstance(value, type) and issubclass(value, TestBase) and value.__name__.endswith("Test"):
            value.__requires__ = (*getattr(value, "__requires__", ()), "sqlalchemy_2")


_require_sqlalchemy_2(globals())

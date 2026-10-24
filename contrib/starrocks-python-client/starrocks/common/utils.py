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

import re
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union

from sqlalchemy import schema as sa_schema
from sqlalchemy.exc import StatementError

from starrocks.common.params import DialectName, TableInfoKey
from starrocks.common.types import TableDistribution
from starrocks.engine.interfaces import ReflectedDistributionInfo, ReflectedPartitionInfo, ReflectedTableKeyInfo


class SQLParseError(StatementError):
    """A parse error occurred during execution of a SQL statement."""

    def __init__(self, message: str, statement: Optional[str]):
        super().__init__(message, statement)


class CaseInsensitiveDict(dict):
    """A dictionary that enables case insensitive searching while preserving case sensitivity when keys are set."""

    def __init__(self, data: Optional[Union[Dict[str, Any], Mapping[str, Any]]] = None) -> None:
        super().__init__()
        self._lower_keys: Dict[str, str] = {}  # Maps lowercase keys to actual keys
        if data:
            self.update(data)

    def __setitem__(self, key: str, value: Any) -> None:
        lower_key: str = key.lower()
        if lower_key in self._lower_keys:
            # Remove old entry if exists
            old_key: str = self._lower_keys[lower_key]
            super().__delitem__(old_key)
        self._lower_keys[lower_key] = key
        super().__setitem__(key, value)

    def __getitem__(self, key: str) -> Any:
        lower_key: str = key.lower()
        if lower_key in self._lower_keys:
            actual_key: str = self._lower_keys[lower_key]
            return super().__getitem__(actual_key)
        raise KeyError(key)

    def get(self, key: str, default: Optional[Any] = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        return key.lower() in self._lower_keys

    def update(self, other: Union['CaseInsensitiveDict', Mapping[str, Any], Iterator[Tuple[str, Any]]]) -> None:
        if hasattr(other, 'items'):
            for key, value in other.items():
                self[key] = value
        else:
            for key, value in other:
                self[key] = value


def extract_dialect_options_as_case_insensitive(
    table: Union[sa_schema.Table, sa_schema.Column]
) -> CaseInsensitiveDict:
    """
    Extract StarRocks dialect-specific options and return as a CaseInsensitiveDict.

    This function is useful when extracting options from Table.dialect_options or similar,
    filtering out None values and enabling case-insensitive key lookups.

    Args:
        dialect_options: The dialect_options dict (e.g., Table.dialect_options)

    Returns:
        CaseInsensitiveDict with non-None values

    Example:
        >>> opts = extract_dialect_options_as_case_insensitive(table)
        >>> security = opts.get('SECURITY')  # Works with any case
    """
    raw_opts = table.dialect_options.get(DialectName, {})
    return CaseInsensitiveDict({k: v for k, v in raw_opts.items() if v is not None})


def inferred_primary_key_columns(table: sa_schema.Table) -> Optional[List[str]]:
    """Return the key columns of a table that marks a primary key but names no StarRocks key model.

    Such a table, with ``primary_key=True`` columns and no ``starrocks_primary_key`` or other ``starrocks_*_key``,
    is created as a PRIMARY KEY table on those columns, in their table order, so CREATE TABLE and the autogenerate
    comparison agree on its key. Returns None when the table names a key model or has no primary key columns.
    """
    opts = extract_dialect_options_as_case_insensitive(table)
    if any(key_kwarg in opts for key_kwarg in TableInfoKey.KEY_KWARG_MAP):
        return None
    return [column.name for column in table.columns if column.primary_key] or None


def get_dialect_option(
    table: sa_schema.Table,
    key: str,
    default: Optional[Any] = None,
) -> Any:
    """
    Get a StarRocks dialect-specific option value with case-insensitive key lookup.

    This is a convenience function that combines extraction and lookup in one call.
    Useful when you only need to retrieve a single option value.

    Args:
        Table: in which there is the dialect_options dict (e.g., Table.dialect_options)
        key: The option key to retrieve (case-insensitive)
        default: Default value if key not found

    Returns:
        The option value, or default if not found

    Example:
        >>> from starrocks.common.params import TableInfoKey
        >>> security = get_dialect_option(table, TableInfoKey.SECURITY)
    """
    opts = extract_dialect_options_as_case_insensitive(table)
    return opts.get(key, default)


class TableAttributeNormalizer:
    """A class to normalize StarRocks attributes for comparison."""

    # Pre-compiled regex patterns for better performance
    _BACKTICKS_PATTERN = re.compile(r'`([^`]+)`')
    _WHITESPACE_PATTERN = re.compile(r'\s+')
    # Matches spaces around opening parenthesis
    _OPEN_PAREN_SPACE_PATTERN = re.compile(r'\s*(\()\s*')
    # Matches spaces around closing parenthesis
    _CLOSE_PAREN_SPACE_PATTERN = re.compile(r'\s*(\)\s?)\s*')
    _COMMA_SPACE_PATTERN = re.compile(r'\s*,\s*')

    @staticmethod
    def strip_identifier_backticks(sql: str) -> str:
        """Remove MySQL-style identifier quotes (`) while preserving string literals."""
        result = []
        in_string = False
        in_backtick = False
        quote_char = None

        i = 0
        while i < len(sql):
            ch = sql[i]

            if in_string:
                result.append(ch)
                if ch == quote_char:
                    # ignore quote with escape character
                    if i == 0 or sql[i - 1] != '\\':
                        in_string = False

            elif in_backtick:
                if ch == '\\' and i + 1 < len(sql):
                    # support MySQL-style backtick escape in backtick, e.g., `col\`name`
                    next_ch = sql[i + 1]
                    if next_ch == '`' or next_ch == '\\':
                        result.append(next_ch)
                        i += 1
                    else:
                        result.append(ch)
                elif ch == '`':
                    in_backtick = False
                else:
                    result.append(ch)

            else:
                if ch == '`':
                    in_backtick = True
                elif ch in ("'", '"'):
                    in_string = True
                    quote_char = ch
                    result.append(ch)
                else:
                    result.append(ch)

            i += 1

        return ''.join(result)

    @staticmethod
    def _strip_line_comments(sql: str) -> str:
        """Replace ``-- ...`` line comments with a space, skipping ``--`` inside string
        literals or backtick identifiers (where it is data, not a comment)."""
        result: List[str] = []
        in_string = False
        in_backtick = False
        quote_char = None
        i = 0
        n = len(sql)
        while i < n:
            ch = sql[i]
            if in_string:
                result.append(ch)
                if ch == quote_char and sql[i - 1] != '\\':
                    in_string = False
            elif in_backtick:
                result.append(ch)
                if ch == '`':
                    in_backtick = False
            elif ch == '`':
                in_backtick = True
                result.append(ch)
            elif ch in ("'", '"'):
                in_string = True
                quote_char = ch
                result.append(ch)
            elif ch == '-' and i + 1 < n and sql[i + 1] == '-':
                # Replace "-- ... \n" (newline included) with one space.
                result.append(' ')
                i += 2
                while i < n and sql[i] != '\n':
                    i += 1
                i += 1
                continue
            else:
                result.append(ch)
            i += 1
        return ''.join(result)

    @staticmethod
    def _collapse_whitespace_outside_strings(sql: str) -> str:
        """Collapse runs of whitespace to a single space, leaving whitespace inside string
        literals untouched so that e.g. ``'a   b'`` and ``'a b'`` stay distinct."""
        result: List[str] = []
        in_string = False
        quote_char = None
        i = 0
        n = len(sql)
        while i < n:
            ch = sql[i]
            if in_string:
                result.append(ch)
                if ch == quote_char and sql[i - 1] != '\\':
                    in_string = False
            elif ch in ("'", '"'):
                in_string = True
                quote_char = ch
                result.append(ch)
            elif ch.isspace():
                result.append(' ')
                while i + 1 < n and sql[i + 1].isspace():
                    i += 1
            else:
                result.append(ch)
            i += 1
        return ''.join(result)

    @staticmethod
    def normalize_sql(sql: Optional[str], lowercase: bool = True, remove_qualifiers: bool = False) -> Optional[str]:
        """Normalize an SQL string for comparison (string hygiene only, no semantic
        canonicalization — that lives in :mod:`starrocks.common.sql_canonical`).

        Lowercases, strips ``--`` comments and backtick quotes, collapses whitespace and
        trailing semicolons (all string-literal-safe), and optionally removes ``schema.table.``
        qualifiers.
        """
        if sql is None:
            return None
        sql = sql.replace('\\n', '\n').replace('\\t', '\t')
        # MySQL-style escaping: 'O\'Brien' -> 'O''Brien'
        sql = sql.replace("\\'", "''")

        sql = TableAttributeNormalizer._strip_line_comments(sql)
        if lowercase:
            sql = sql.lower().strip()

        if remove_qualifiers:
            sql = re.sub(r"((?:`[^`]+`|\w+)\.)+", "", sql)

        sql = TableAttributeNormalizer.strip_identifier_backticks(sql)

        sql = TableAttributeNormalizer._collapse_whitespace_outside_strings(sql).strip()
        # Strip trailing semicolons together with any surrounding whitespace.
        sql = sql.rstrip(" ;")
        return sql

    @staticmethod
    def _simple_normalize(value: str) -> str:
        return value.upper().strip() if value else value

    @staticmethod
    def normalize_engine(engine: str) -> Optional[str]:
        return TableAttributeNormalizer._simple_normalize(engine)

    @staticmethod
    def normalize_key(table_key: Union[ReflectedTableKeyInfo, str]) -> Optional[str]:
        """Normalize table key string by removing backticks and extra spaces.
        Because there may be column names in this string, we don't simply lowercase it.
        If the table key is a ReflectedTableKeyInfo object, return the string representation only.
        """
        if not table_key:
            return table_key
        return TableAttributeNormalizer.normalize_column_identifiers(
            str(table_key) if isinstance(table_key, ReflectedTableKeyInfo) else table_key
        )

    @staticmethod
    def normalize_partition_method(partition: Union[ReflectedPartitionInfo, str]) -> Optional[str]:
        """Normalize partition string by removing backticks and extra spaces.
        Because there may be column names in this string, we don't simply lowercase it.
        If the partition is a ReflectedPartitionInfo object, return the partition_method only.
        """
        if not partition:
            return partition
        return TableAttributeNormalizer.normalize_column_identifiers(
            partition.partition_method if isinstance(partition, ReflectedPartitionInfo) else partition
        )

    @staticmethod
    def normalize_distribution_string(distribution: Union[ReflectedDistributionInfo, str]) -> Optional[str]:
        """Normalize distribution string by removing backticks and extra spaces.
        Because there may be column names in this string, we don't simply lowercase it.
        If the distribution is a ReflectedDistributionInfo object, return the string representation only.
        """
        if not distribution:
            return distribution
        return TableAttributeNormalizer.normalize_column_identifiers(
            str(distribution) if isinstance(distribution, ReflectedDistributionInfo) else distribution
        )

    @staticmethod
    def normalize_order_by_string(order_by: Union[str, List[str], None]) -> Optional[str]:
        """Normalize ORDER BY string by removing backticks and standardizing format.
        Because there may be column names in this string, we don't simply lowercase it.
        """
        if not order_by:
            return order_by
        elif isinstance(order_by, list):
            order_by = ', '.join(str(item) for item in order_by)
        order_by = TableAttributeNormalizer.remove_outer_parentheses(order_by)
        return TableAttributeNormalizer.normalize_column_identifiers(order_by)

    @staticmethod
    def normalize_column_identifiers(text: str) -> Optional[str]:
        """Normalize column identifiers by removing backticks, standardizing spaces, and removing spaces inside parentheses.
        Because there may be column names in this string, we don't simply lowercase it.
        """
        if not text:
            return text
        normalized: str = TableAttributeNormalizer._BACKTICKS_PATTERN.sub(r'\1', text)
        normalized = TableAttributeNormalizer._WHITESPACE_PATTERN.sub(' ', normalized).strip()
        # Remove spaces immediately around opening parenthesis
        normalized = TableAttributeNormalizer._OPEN_PAREN_SPACE_PATTERN.sub(r'\1', normalized)
        # Remove spaces immediately around closing parenthesis
        normalized = TableAttributeNormalizer._CLOSE_PAREN_SPACE_PATTERN.sub(r'\1', normalized)
        # Standardize spaces around commas within parentheses
        normalized = TableAttributeNormalizer._COMMA_SPACE_PATTERN.sub(', ', normalized)
        return normalized

    @staticmethod
    def remove_outer_parentheses(text: str) -> str:
        """Remove outer parentheses from text."""
        if not text:
            return text
        text = text.strip()
        if text.startswith('(') and text.endswith(')'):
            return text[1:-1].strip()
        return text

    @staticmethod
    def simply_normalize_quotes(text: Optional[str]) -> Optional[str]:
        """Simply normalize double quotes to single quotes."""
        if not text:
            return text
        return text.replace('"', "'")


_RANGE_DISTRIBUTION_PATTERN = re.compile(r'^\s*RANGE\b', re.IGNORECASE)


def is_range_distribution(distribution: Union[ReflectedDistributionInfo, str, None]) -> bool:
    """Whether a DISTRIBUTED BY value denotes range distribution.

    Accepts a reflected value such as 'RANGE BUCKETS 1' or a metadata value such as 'RANGE'.
    """
    if not distribution:
        return False
    if isinstance(distribution, ReflectedDistributionInfo):
        if distribution.type and not distribution.distribution_method:
            return str(distribution.type).strip().upper() == TableDistribution.RANGE
        distribution = str(distribution)
    return bool(_RANGE_DISTRIBUTION_PATTERN.match(distribution))


def find_matching_parenthesis(text: str, start_index: int = 0) -> int:
    """
    Finds the index of the matching closing parenthesis for a given starting parenthesis.

    Args:
        text: The string to search within.
        start_index: The index of the opening parenthesis.

    Returns:
        The index of the matching closing parenthesis, or -1 if not found.

    Note:
        The first byte is '(', we need to skip it.
    """
    open_paren_count = 1
    for i in range(start_index + 1, len(text)):
        if text[i] == '(':
            open_paren_count += 1
        elif text[i] == ')':
            open_paren_count -= 1
            if open_paren_count == 0:
                return i
    return -1


def gen_simple_qualified_name(table_name: str, schema: Optional[str] = None) -> str:
    """Generate a simple qualified name for a table."""
    return f"{schema}.{table_name}" if schema else table_name


_SQL_STRING_LITERAL_BODY = re.compile(r"(?:[^'\\]|''|\\.)*", re.DOTALL)
_SQL_STRING_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\x00", "b": "\b", "Z": "\x1a"}
_FUNCTION_DEFAULT = re.compile(r"[A-Za-z_]\w*\s*\(.*\)", re.DOTALL)
_KEYWORD_DEFAULT = re.compile(r"[A-Za-z_]\w*")


def quote_sql_string_literal(value: str) -> str:
    """
    Render a string as the SQL literal StarRocks accepts: quotes doubled, backslashes and NUL escaped.

    This is what SQLAlchemy emits for ``server_default="..."``, so a reflected default renders back to the DDL
    that created it.
    """
    escaped = value.replace("\\", "\\\\").replace("'", "''").replace("\x00", "\\0")
    return f"'{escaped}'"


def unquote_sql_string_literal(text: str) -> Optional[str]:
    """
    Return the value of a single-quoted SQL string literal, or None if ``text`` is not one.
    """
    text = text.strip()
    if len(text) < 2 or text[0] != "'" or text[-1] != "'" or not _SQL_STRING_LITERAL_BODY.fullmatch(text, 1, len(text) - 1):
        return None
    body = text[1:-1].replace("''", "'")
    return re.sub(r"\\(.)", lambda m: _SQL_STRING_ESCAPES.get(m.group(1), m.group(1)), body, flags=re.DOTALL)


def strip_wrapping_parentheses(text: str) -> str:
    """
    Remove the parentheses that wrap the whole expression, however many, as in ``((uuid()))``.
    """
    text = text.strip()
    while text.startswith("(") and find_matching_parenthesis(text, 0) == len(text) - 1:
        text = text[1:-1].strip()
    return text


def canonical_server_default(sql: str) -> Tuple[str, str]:
    """
    Reduce a column default, as SQL text, to a ``(kind, value)`` pair that is equal for equal defaults.

    - ``("literal", value)`` for a quoted string literal, compared by its decoded value;
    - ``("expression", text)`` for a function call or keyword such as ``uuid()`` or ``CURRENT_TIMESTAMP``, with
      wrapping parentheses removed and the case and spacing normalised, as these are case insensitive;
    - ``("literal", text)`` for anything else, such as a bare number.
    """
    unwrapped = strip_wrapping_parentheses(sql)
    if (decoded := unquote_sql_string_literal(unwrapped)) is not None:
        return "literal", decoded
    if _FUNCTION_DEFAULT.fullmatch(unwrapped) or _KEYWORD_DEFAULT.fullmatch(unwrapped):
        return "expression", re.sub(r"\s+", "", unwrapped).lower()
    return "literal", unwrapped

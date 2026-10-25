Version history
===============

**Unreleased**

- Upgrading from 1.3.x:
  - `primary_key=True` columns with no `starrocks_*_key` make a PRIMARY KEY table when they are the table's first columns, in key order. Otherwise the dialect warns and sends no key clause, and StarRocks makes a DUPLICATE KEY table, as 1.3 did for every such table. Autogenerate leaves existing DUPLICATE KEY tables alone. To keep a new table DUPLICATE KEY, name its key with `starrocks_duplicate_key`.
  - A DUPLICATE KEY table reflects no primary key, so `automap_base()` no longer maps it. A reflected ORM class needs `Column(..., primary_key=True)` or `__mapper_args__ = {"primary_key": [...]}`.
  - Write `%` once in DDL text: view and materialized view definitions, `starrocks_partition_by`, `starrocks_distributed_by`, `starrocks_order_by`, `starrocks_refresh`, `starrocks_properties` and the `op.alter_*` operations. 1.3 raised on a single `%`, so a `%%` written for it now reaches StarRocks as `%%`, and the dialect warns about it.
  - Connections report `AUTOCOMMIT`, where they reported `REPEATABLE READ`. `isolation_level="READ COMMITTED"` runs DML in StarRocks transactions, so it needs StarRocks 3.5 or later, and `enable_sql_transaction` from 4.0. `READ UNCOMMITTED`, `REPEATABLE READ` and `SERIALIZABLE` are deprecated: they run as `AUTOCOMMIT`, as before, with a `FutureWarning`.
  - DOUBLE values read as `float` (1.3 returned `Decimal`), LARGEINT as `int`, MAP and STRUCT values as Python objects even where their text isn't JSON, and ARRAY<JSON> elements as JSON values (1.3 returned the server's text).
  - `ViewSecurityType.DEFINER` is deprecated. StarRocks has no `SECURITY DEFINER`, so a view given it raises `CompileError`.
  - The `test` extra installs SQLAlchemy below 2.1, which is untested. The runtime dependency is unchanged.
- Tests: every test runs in one `pytest` run under SQLAlchemy's testing plugin, in parallel, together with SQLAlchemy's and Alembic's dialect suites (Alembic's on SQLAlchemy 2.0 only). Worker databases are dropped with `FORCE`. Tested on StarRocks 3.3.22, 3.5.21 and 4.1.6, SQLAlchemy 2.0 and 1.4, and Alembic 1.14.1 to 1.20 (by @rad-pat)
- Queries and DML:
  - An executemany whose VALUES hold more than bare placeholders, such as a JSON, ARRAY, MAP or STRUCT column, goes as multi-row INSERTs instead of one INSERT per row, which was about 100 times slower and failed past about 1,000 rows. ORM flushes and Alembic's `bulk_insert` are included.
  - Any executemany over 10,000 rows is split into INSERTs of at most 10,000 rows, which StarRocks requires.
  - `insert(...).with_dialect_options(starrocks_overwrite=True)` sends `INSERT OVERWRITE`, and `starrocks_label=` sends `INSERT ... WITH LABEL`. An executemany of either goes as one INSERT, and raises only when the rows need more than one.
  - A multi-table UPDATE renders as `UPDATE ... FROM`, and a multi-table DELETE with `USING`. `starrocks_limit` is refused on UPDATE and DELETE.
  - An OFFSET without LIMIT gets the LIMIT StarRocks needs, and `func.sysdate(n)` keeps its precision.
  - A CAST names the integer type asked for, with `sa.Integer` as BIGINT and unsigned types widened to a signed type. REAL compiles as FLOAT (by @rad-pat)
- JSON:
  - Index and path access render with `json_query` and the `get_json_*` functions; StarRocks has no `JSON_EXTRACT`. A chain of accesses reads in one path.
  - A key holding `\` is named exactly, and a key no StarRocks path can name raises.
  - `as_numeric(p, s)` keeps every digit, and JSON scalars round-trip as scalars.
  - Known issue: from StarRocks 4.0, Flat JSON stores a JSON null as `{}` when it shares an INSERT, or later a compaction, with JSON objects (StarRocks issue 81490). `"flat_json.enable" = "false"` keeps the null (by @rad-pat)
- Types:
  - `PERCENTILE` compiles. `PERCENTILE_UNION` and the agg-state aggregate types are accepted, and `COUNT`, which StarRocks doesn't have, is dropped.
  - BINARY and VARBINARY keep their lengths. `Text` compiles as STRING, `sa.Enum` as a VARCHAR as long as its longest value, and NCHAR or a collated CHAR as CHAR.
  - STRUCT field names are quoted with the identifier preparer, and a field name holding a backquote raises. STRUCT columns that StarRocks 4.1 shows with backquoted field names reflect as STRUCT, and LARGEINT reflects.
  - An aware datetime is stored as its wall time, and a `date` renders as a DATETIME literal (by @rad-pat)
- ARRAY, MAP and STRUCT:
  - Values bind element by element through each element's own type, so quotes, decimals, dates, binary, JSON, TypeDecorators and nested values round-trip exactly on StarRocks 3.3 to 4.1. The generic `sa.ARRAY` is adapted, its `dimensions` included.
  - Array text such as `"[1,2,3]"` still binds. Text that isn't a JSON array goes through StarRocks' own cast, as before.
  - Values are read with `json` first, and with the dialect's own scanner for what isn't JSON. Each column's converters are built once, so a SELECT of such columns is faster than 1.3's.
  - A CAST of a string to MAP or STRUCT, which StarRocks can't run, is left out, and the text is read as the type.
  - `starrocks+asyncmy` binds binary values as `X'...'` (by @rad-pat)
- Views and materialized views:
  - `create_all` and `drop_all` handle the views and materialized views in a `MetaData`, creating each after what it reads, a `text()` definition included.
  - A `select()` definition is compiled for StarRocks. Comments are escaped. Comment and security changes are applied with `CREATE OR REPLACE VIEW` and `ALTER VIEW ... SET SECURITY`.
  - A materialized view with neither `starrocks_refresh` nor `starrocks_distributed_by` is StarRocks' synchronous materialized view. It is created as before, now with a logged warning, because reflection and autogenerate can't see it.
  - `REFRESH ASYNC` is sent for the `SCHEDULE` spelling, which StarRocks before 4.1.1 doesn't know (by @rad-pat)
- Tables and indexes:
  - `CREATE INDEX` follows StarRocks' grammar, with `starrocks_using`, `starrocks_properties` and `starrocks_comment`. The dialect waits for the index's build job, and warns if StarRocks cancels it.
  - PROPERTIES keys and values are escaped.
  - `starrocks_order_by` takes a list of column names, which are quoted.
  - A PARTITION BY a column or a list of columns gets the parentheses StarRocks 3.3 needs.
  - Names StarRocks reserves and MySQL doesn't, such as `files`, `text` and `json`, are quoted.
  - `VARIANT` columns are refused before StarRocks 4.1, and view `starrocks_security` before 3.4.1 (by @rad-pat)
- Reflection:
  - Only PRIMARY, UNIQUE and AGGREGATE KEY tables report a primary key. A table is reflected once instead of twice, and information_schema is read once per schema.
  - Secondary indexes reflect from `SHOW INDEX`, with their type, properties and comment.
  - String defaults, AUTO_INCREMENT, PARTITION BY, and a materialized view's REFRESH and PROPERTIES are read from the clauses themselves, so a comment can't change them. That includes StarRocks 3.3's unescaped quotes in PRIMARY KEY comments.
  - Views and materialized views can be inspected through an `Engine`. A materialized view's columns reflect in SELECT order, and its definition without a trailing `;`.
  - StarRocks 3.5's generated partition column is left out, and its expression put back into the partition clause (by @rad-pat)
- Autogenerate:
  - A change StarRocks can't ALTER is skipped with a warning, and the rest of the revision is generated. That covers a key type, a partition method, a column default, and the table and materialized view properties StarRocks refuses to SET.
  - A materialized view whose definition changed is dropped and created again, keeping the database's refresh and the properties the model leaves out.
  - Before StarRocks 4.0.6, an unchanged materialized view is no longer recreated when its definition can't be compared through a temporary view.
  - View and materialized view operations are ordered by what each reads, so both directions of a revision run.
  - Differences that are only spelling are no longer reported: function and quoted-literal defaults, `time_slice` partitions, an omitted MV refresh moment, `bloom_filter_columns` order, and index names that differ only in case.
  - On SQLAlchemy 1.4, autogenerate compares tables, and leaves the database's views and materialized views alone with a warning (by @rad-pat)
- Alembic operations:
  - `op.alter_column` renames in an ALTER of its own, and restates KEY or the aggregate type in MODIFY COLUMN. It leaves an unchanged AUTO_INCREMENT out, and refuses a changed one. Offline, `autoincrement=False` leaves it out.
  - `op.alter_table_properties` sends one SET per property group.
  - `op.drop_index` matches names without regard to case, and skips only an index an earlier `drop_column` in the run took, or a missing one with `if_exists=True`.
  - Constraint operations StarRocks can't run, and a column default change, raise.
  - `op.rename_table` sends `RENAME` without `TO`.
  - Offline literals of ARRAY, MAP and STRUCT values are exact (by @rad-pat)
- Transactions and DDL execution:
  - With `isolation_level="READ COMMITTED"`, the first INSERT, UPDATE or DELETE opens a StarRocks transaction. COMMIT and ROLLBACK reach StarRocks. DDL commits first, and statements StarRocks refuses inside a transaction raise before they are sent.
  - Each `ALTER TABLE`, `CREATE INDEX` and `DROP INDEX` waits for the table's running schema change, for at most `starrocks_schema_change_timeout` seconds (`create_engine()` option, default 300). Then it raises `SchemaChangeTimeoutError`. `starrocks_wait_for_schema_change=False` turns the wait off.
  - Every statement's error is reported from a DDL construct that compiles to several statements, including over asyncmy and `async_fallback` (by @rad-pat)
- Autogenerate no longer resets a table or materialized view property the model omits back to a hardcoded default; it is left as the database has it. Many defaults depend on the StarRocks version or FE config (`replication_num`, `fast_schema_evolution`, `bucket_size`), so the reset was often spurious. To manage a property, declare it in the model (by @chris-celerdata)
- Stop phantom property ALTERs when StarRocks does not report a value the model sets: add defaults for properties only reported when non-default (`write_quorum`, `compaction_strategy`, `enable_load_profile`, `mutable_bucket_num`, `primary_index_cache_expire_sec`, `storage_type`), compare enum and boolean values case-insensitively, and warn instead of generating an ALTER for a property the table cannot hold (`storage_format`, `bucket_size` on a non-RANDOM table, `enable_persistent_index` on a non-PRIMARY KEY table) (by @chris-celerdata)
- Stop autogenerate from resetting an unmanaged `bucket_size` on RANDOM-distributed tables to a hardcoded 4 GiB default; StarRocks 4.0+ defaults to 1 GiB (by @chris-celerdata)
- Reflect `TIME` and `VARIANT` columns instead of returning `NullType` (#77259 by @rad-pat)
- Add the `starrocks_temp_view_schema` Alembic option (`context.configure(...)`) to designate
  the schema in which the transient view used to canonicalize view/MV definitions is created.
  Lets a locked-down migration user be granted the required privileges on a single schema
  (e.g. the same one as `version_table_schema`) instead of on every schema that holds a view.
- Coalesce multiple `ADD`/`DROP COLUMN` autogenerate operations on a table into a single `ALTER TABLE` statement via the `combine_column_alters` rewriter, avoiding StarRocks in-progress schema-change failures (#78647 by @chris-celerdata)
- Add opt-in `starrocks_wait_for_schema_change` to block until a column schema-change job reaches a terminal state (#78647 by @chris-celerdata)

**1.3.4**

- Add `to_diff_tuple` for Alembic alter operations (#70146 by @arvindKandpal-ksolves)
- Fix parsing of reflected nested `STRUCT` / `ARRAY` / `MAP` column types when inline field comments are present (#69817)
- Deserialize complex types to matching Python list, dict types (#70480 by @chris-celerdata)
- Add `__hash__` to reflected dataclasses to fix unhashable type errors (#70734 by @aholowko)
- Fix spurious Alembic regeneration for syntax and property changes using SQLGlot (#75984 by @chris-celerdata)
- Add `SCHEDULE` keyword to the Python parser (#76762 by @chris-celerdata)

**1.3.3**

- Add back support for SQLAlchemy 1.4 (#65976 by @rad-pat)
- Enable select from a FilesSource and support for Python 3.14 (#66797 by @rad-pat)
- Add support for async SQLAlchemy connection via asyncmy driver and
  ignore errors from querying tables_config on non-default catalog (#67479 by @rad-pat)

**1.3.2**
- Fix DEFAULT compilation, type comparison, and column ordering (#66125 by @jaogoy)

**1.3.1**
- Enable SQLAlchemy Test Suite (#65025 by @rad-pat)
- Supports Views and Materialized Views and enhance documentation (#65808 by @jaogoy)

**1.3.0**
- Support more attributes for comparing StarRocks tables by using Alembic (#64161 by jaogoy)

**1.2.3**
Older
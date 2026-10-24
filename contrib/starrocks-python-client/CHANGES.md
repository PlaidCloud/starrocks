Version history
===============

**Unreleased**

- Upgrading from 1.3.x:
  - `primary_key=True` columns with no `starrocks_*_key` make a PRIMARY KEY table where StarRocks can hold one. Otherwise the dialect warns and sends no key clause, and StarRocks makes a DUPLICATE KEY table, as 1.3 did for every such table. Autogenerate leaves existing DUPLICATE KEY tables alone. To keep a new table DUPLICATE KEY, name its key with `starrocks_duplicate_key`.
  - StarRocks holds a PRIMARY KEY table when its key columns come first, in key order, are BOOLEAN, integer, DATE, DATETIME or VARCHAR columns, none of them generated, and fit the 128 bytes StarRocks loads in a row's key (a `VARCHAR(n)` counts n, and 2 more when another key column follows). The table must be distributed by HASH of key columns or by default, partitioned by key columns only, sorted by columns of those types or STRING, and free of the properties StarRocks refuses for a PRIMARY KEY table. So a `sa.Uuid()` (CHAR(32)), DECIMAL, `String(255)` or `Text` key, RANDOM distribution, or a partition or HASH column outside the key still gives a DUPLICATE KEY table, now with the warning. A table of another engine gets no key clause and no warning.
  - A DUPLICATE KEY table reflects no primary key, so `automap_base()` no longer maps it. A reflected ORM class needs `Column(..., primary_key=True)` or `__mapper_args__ = {"primary_key": [...]}`.
  - Write `%` once in DDL text: view and materialized view definitions, `starrocks_partition_by`, `starrocks_distributed_by`, `starrocks_order_by`, `starrocks_refresh`, `starrocks_properties` and the `op.alter_*` operations. 1.3 raised on a single `%`, so a `%%` written for it now reaches StarRocks as `%%`, and the dialect warns about it. The warning names the filter that silences it for a `%%` you mean.
  - Offline SQL (`--sql`) needs `dialect_opts={"paramstyle": "named"}` in `run_migrations_offline()`, as Alembic's `env.py` template has, or each `%` in DDL text is written doubled.
  - Connections report `AUTOCOMMIT`, where they reported `REPEATABLE READ`. Each new connection starts in it whatever the driver's autocommit setting, so a URL's `autocommit=false`, `connect_args={"autocommit": False}`, and `creator` and `async_creator` connections run as `AUTOCOMMIT`, as in 1.3. `isolation_level="READ COMMITTED"` runs DML in StarRocks transactions, so it needs StarRocks 3.5 or later, and `enable_sql_transaction` from 4.0. `READ UNCOMMITTED`, `REPEATABLE READ` and `SERIALIZABLE` are deprecated: they run as `AUTOCOMMIT`, as before, with a `FutureWarning`, given once when `create_engine()` gets one.
  - A `BEGIN` or `START TRANSACTION` sent as text raises `InvalidRequestError` where StarRocks would ignore it: before 3.5, and from 4.0 in a session whose `enable_sql_transaction` is off.
  - DOUBLE values read as `float` (1.3 returned `Decimal`), LARGEINT as `int`, MAP and STRUCT values as Python objects even where their text isn't JSON, and ARRAY<JSON> elements as JSON values (1.3 returned the server's text).
  - `func.array_agg()` results, and values read as the generic `sa.ARRAY`, are Python lists with typed elements, where 1.3 returned the server's text, such as `'[1,2]'`. Code that ran `json.loads` on them gets `TypeError`.
  - An ARRAY, MAP or STRUCT value that doesn't match its declared type, such as `ARRAY(INTEGER())` over `["a","b"]`, raises `ValueError` and fails the fetch, where 1.3 returned the server's text. Declare the type the server has.
  - `cast(<JSON>, String)` returns the JSON text, as SQLAlchemy documents it: a JSON string keeps its quotes, `'"s"'`, and a JSON null reads `'null'`. 1.3's CAST read `'s'` and SQL NULL.
  - A CAST to a float type is sent, where 1.3 left it out. The generic `sa.Float` with no precision, and a float type with a precision above 24, such as `Float(53)`, cast to DOUBLE, as MySQL reads `FLOAT(p)`. `FLOAT()`, `FLOAT(24)` or below, `mysql.FLOAT(M, D)` and REAL cast to StarRocks' 4-byte FLOAT, which keeps about 7 significant digits. Columns of these types are still created as FLOAT.
  - `ColumnAggType.COUNT` is removed, so code that names it gets `AttributeError`. StarRocks has no COUNT aggregate type, so no table could use it.
  - `ViewSecurityType.DEFINER` is deprecated. StarRocks has no `SECURITY DEFINER`, so a view given it raises `CompileError`.
  - The `test` extra installs SQLAlchemy below 2.1, which is untested, and the tests need SQLAlchemy 1.4, or 2.0.21 or later. The runtime dependency is unchanged.
- Tests: every test runs in one `pytest` run under SQLAlchemy's testing plugin, in parallel, together with SQLAlchemy's and Alembic's dialect suites (Alembic's on SQLAlchemy 2.0 only). Worker databases, and the databases the tests create, are dropped with `FORCE`. Collection fails for a test the plugin would drop without a word: a module-level function, a misnamed, classmethod or staticmethod test, or a class with tests, nested or not, that the plugin doesn't collect. The asyncmy-only tests run with `--dbdriver asyncmy`. SQLAlchemy's ARRAY round trip runs, and its two literal round trips are skipped, since StarRocks refuses an ARRAY as a table's first column. Tested on StarRocks 3.3.22, 3.5.21 and 4.1.6, SQLAlchemy 2.0 and 1.4, and Alembic 1.14.1 to 1.20 (by @rad-pat)
- Queries and DML:
  - An executemany of ARRAY, MAP and STRUCT values works, as multi-row INSERTs, where 1.3 failed. JSON keeps one multi-row INSERT now that it binds through an expression. An executemany of a VARIANT column, or of SQL expressions in VALUES, goes as multi-row INSERTs instead of one INSERT per row. ORM flushes and Alembic's `bulk_insert` are included.
  - An INSERT executemany of more than 10,000 rows is split into INSERTs of at most 10,000 rows, which StarRocks requires. INSERT text whose VALUES hold more than placeholders is still sent by the driver, one row at a time.
  - `insert(...).with_dialect_options(starrocks_overwrite=True)` sends `INSERT OVERWRITE`, and `starrocks_label=` sends `INSERT ... WITH LABEL`. The label must be 1 to 128 ASCII letters, digits, `_` or `-`, as StarRocks requires, and any other label raises `CompileError`, including one with a backquote, which StarRocks would record under another name. An executemany of either goes as one INSERT, and raises when its rows need more than one, or when the driver would send it one row at a time.
  - A multi-table UPDATE renders as `UPDATE ... FROM`, and a multi-table DELETE with `USING`. `starrocks_limit` is refused on UPDATE and DELETE.
  - An OFFSET without LIMIT gets the LIMIT StarRocks needs, and `func.sysdate(n)` keeps its precision.
  - A CAST names the integer type asked for, with `sa.Integer` as BIGINT and unsigned types widened to a signed type. A CAST to Boolean works on SQLAlchemy 1.4, where it raised `AttributeError`. REAL compiles as FLOAT (by @rad-pat)
- JSON:
  - Index and path access render with `json_query` and the `get_json_*` functions; StarRocks has no `JSON_EXTRACT`. A chain of accesses reads in one path.
  - A key holding `\` is named exactly, and a key no StarRocks path can name raises.
  - `as_numeric(p, s)` reads the value's text, so it keeps every digit StarRocks stores, integers past 2**53 included. StarRocks stores a JSON number with a fraction, in an object or an array, as a DOUBLE, so the digits past a double's precision are lost at INSERT. JSON scalars round-trip as scalars.
  - Known issue: from StarRocks 4.0, Flat JSON stores a JSON null as `{}` when it shares an INSERT, or later a compaction, with JSON objects (StarRocks issue 81490). `"flat_json.enable" = "false"` keeps the null (by @rad-pat)
- Types:
  - `PERCENTILE` compiles. `PERCENTILE_UNION` and the agg-state aggregate types are accepted, and `ColumnAggType.COUNT`, which StarRocks doesn't have, is removed.
  - BINARY and VARBINARY keep their lengths. `Text` compiles as STRING, `sa.Enum` as a VARCHAR that holds its longest value, and NCHAR or a collated CHAR as CHAR. `TIMESTAMP`, which StarRocks doesn't have, compiles as DATETIME, and a DATETIME or TIMESTAMP precision is left out, since StarRocks' DATETIME takes none.
  - STRUCT field names are quoted with the identifier preparer, and a field name holding a backquote raises. STRUCT columns that StarRocks 4.1 shows with backquoted field names reflect as STRUCT.
  - An aware datetime is stored as its wall time, and a `date` renders as a DATETIME literal (by @rad-pat)
- ARRAY, MAP and STRUCT:
  - Values bind element by element through each element's own type, so quotes, decimals, dates, binary, JSON, TypeDecorators, a TypeDecorator over an ARRAY, MAP or STRUCT inside another collection included, and nested values round-trip exactly on StarRocks 3.3 to 4.1. The exception is binary inside a MAP or STRUCT on StarRocks 3.x, which breaks the connection. The generic `sa.ARRAY` is adapted, its `dimensions` included.
  - Array text such as `"[1,2,3]"` still binds. Text that isn't a JSON array goes through StarRocks' own cast, as before.
  - Values are read with `json` first, and with the dialect's own scanner for what isn't JSON. Each column's converters are built once, so the columns 1.3 decoded, such as ARRAY<STRUCT> and ARRAY<DOUBLE>, read faster than in 1.3. MAP<INT, VARCHAR> and ARRAY<JSON> values, which 1.3 returned as text, take longer, since they are now decoded.
  - A CAST to ARRAY, MAP or STRUCT is sent where StarRocks can run it: on an ARRAY, MAP or STRUCT, on NULL, and on JSON where the server casts JSON to the type (MAP from 3.4.3, STRUCT from 3.5.0, and never to DATE, DATETIME, DECIMAL or binary elements). Elsewhere it is left out, as 1.3 left it out, and the value is read from the operand's text. That covers text, which StarRocks casts to ARRAY as no JSON parser would, an expression with no type, and the JSON casts StarRocks lacks.
  - `starrocks+asyncmy` binds binary values as `X'...'` (by @rad-pat)
- Views and materialized views:
  - `create_all` and `drop_all` handle the views and materialized views in a `MetaData`, creating each after what it reads, a `text()` definition included.
  - A `select()` definition is compiled for StarRocks. Comments are escaped. Comment and security changes are applied with `CREATE OR REPLACE VIEW` and `ALTER VIEW ... SET SECURITY`.
  - A materialized view with neither `starrocks_refresh` nor `starrocks_distributed_by` is StarRocks' synchronous materialized view. It is created as before, now with a logged warning, because reflection and autogenerate can't see it.
  - `CREATE OR REPLACE MATERIALIZED VIEW`, which StarRocks doesn't have, raises `CompileError`.
  - `REFRESH ASYNC` is sent for the `SCHEDULE` spelling, which StarRocks before 4.1.1 doesn't know (by @rad-pat)
- Tables and indexes:
  - `CREATE INDEX` follows StarRocks' grammar, with `starrocks_using`, `starrocks_properties` and `starrocks_comment`. The dialect waits for the index's build job, and warns if StarRocks cancels it.
  - An index StarRocks can't build raises `CompileError` instead of reaching it: a unique, multi-column or expression index, `CREATE INDEX IF NOT EXISTS`, `DROP INDEX IF EXISTS`, an unknown `starrocks_using`, `starrocks_properties` without `starrocks_using`, and MySQL's `starrocks_length`, `starrocks_prefix` and `starrocks_with_parser`.
  - PROPERTIES keys and values are escaped, and a property whose value is `None` raises `CompileError`, where 1.3 sent `"None"`.
  - `starrocks_order_by` takes a list of column names, and quotes each bare name as StarRocks needs. An item holding a space, a parenthesis or a backquote is sent as written.
  - A PARTITION BY a column or a list of columns gets the parentheses StarRocks 3.3 needs.
  - Names StarRocks reserves and MySQL doesn't, such as `files`, `text` and `json`, are quoted.
  - `VARIANT` columns are refused before StarRocks 4.1, and view `starrocks_security` before 3.4.1 (by @rad-pat)
- Reflection:
  - Only PRIMARY, UNIQUE and AGGREGATE KEY tables report a primary key. A table is reflected once instead of twice, and on SQLAlchemy 2.0 information_schema is read once per schema.
  - Secondary indexes reflect from `SHOW INDEX`, with their type, properties and comment.
  - String defaults, AUTO_INCREMENT, PARTITION BY, and a materialized view's REFRESH and PROPERTIES are read from the clauses themselves, so a comment can't change them. That includes StarRocks 3.3's unescaped quotes in PRIMARY KEY comments, and a multi-line comment with a line that starts with a column's name.
  - A partition named like a clause keyword, such as `p_texas` or `` `as` ``, no longer cuts the reflected PARTITION BY short. A materialized view's REFRESH, PROPERTIES and AS SELECT are found the same way, and a REFRESH clause the grammar can't read keeps its `ASYNC`.
  - Views and materialized views can be inspected through an `Engine`. A materialized view's columns reflect in SELECT order, and its definition without a trailing `;`.
  - StarRocks 3.5's generated partition column is left out, and its expression put back into the partition clause (by @rad-pat)
- Autogenerate:
  - A change StarRocks can't ALTER is skipped with a warning, and the rest of the revision is generated. That covers a key type, a partition method, a column default, and the table and materialized view properties StarRocks refuses to SET.
  - A table attribute the model leaves out, such as DISTRIBUTED BY or ORDER BY, is left as the database has it, where 1.3 raised `NotImplementedError` asking for it.
  - Unique constraints and foreign keys, which StarRocks tables can't hold, are left out of the comparison, so the same `create_unique_constraint` or `create_foreign_key` is no longer proposed on every run.
  - A materialized view whose definition changed is dropped and created again, where 1.3 raised `NotImplementedError`. The new one keeps the database's refresh, and the properties the model leaves out unless they hold StarRocks' default. It loses those CREATE refuses for it, which the warning names: `auto_refresh_partitions_limit` and the `partition_*` properties for an MV with no PARTITION BY, `auto_refresh_partitions_limit` for a MANUAL one, and `excluded_trigger_tables` and `excluded_refresh_tables` for one not refreshed ASYNC.
  - Before StarRocks 4.0.2 for a materialized view, and 4.0.7 for a view, a definition that can't be compared through a temporary view counts as changed only when the change is certain: only the model's definition fails to go through the temporary view, as when it reads a column the revision adds; it reads what the revision creates; or the database's reads what the revision drops. Otherwise autogenerate warns and generates no operation for it.
  - A `%` in a view or materialized view definition, as in `date_format(dt, '%Y-%m')` or `LIKE 'x%'`, no longer stops the temporary-view comparison, which altered such views on every run before StarRocks 4.0.7.
  - A change inside a string literal of a definition, such as `'Active'` to `'ACTIVE'`, is detected, and so is a changed decimal number when the definitions are compared as text.
  - View and materialized view operations are ordered by what each reads, so both directions of a revision run.
  - Differences that are only spelling are no longer reported: function and quoted-literal defaults, `time_slice` partitions, an omitted MV refresh moment, `bloom_filter_columns` order, index names that differ only in case, and the `dynamic_partition.start`, `start_day_of_week` and `start_day_of_month` that StarRocks adds.
  - On SQLAlchemy 1.4, autogenerate compares tables, and leaves the database's views and materialized views alone with a warning (by @rad-pat)
- Alembic operations:
  - `op.alter_column` renames in an ALTER of its own, and restates KEY or the aggregate type in MODIFY COLUMN. It leaves an unchanged AUTO_INCREMENT out, and refuses a changed one. Offline, `autoincrement=False` leaves it out.
  - `op.alter_column` with a `server_default` and no `existing_server_default` compares it with the column's default in the database, so restating an unchanged default for a type change works, and a changed one raises. Offline it is sent as given.
  - `op.alter_table_properties` sends one SET per property group, and `op.alter_materialized_view` sends a refresh and properties as two statements. These and `op.alter_view` compile all their statements before running the first, so a security the server lacks, or a property with no value, changes nothing.
  - `op.alter_view` takes `starrocks_security=`, as `op.create_view` spells it, as well as `security=`.
  - `op.drop_index` matches names without regard to case, and skips only an index an earlier `drop_column` in the run took, under a `schema_translate_map` too, or a missing one with `if_exists=True`.
  - `combine_column_alters` combines a table's column adds before a change of its sort key or properties, and the drops after it, so such a table gets two statements. StarRocks refused a `bloom_filter_columns` SET after the combined drop.
  - Constraint operations StarRocks can't run, and a column default change, raise.
  - `op.rename_table` sends `RENAME` without `TO`.
  - Offline literals of ARRAY, MAP and STRUCT values are exact (by @rad-pat)
- Transactions and DDL execution:
  - With `isolation_level="READ COMMITTED"`, the first INSERT, UPDATE or DELETE opens a StarRocks transaction. COMMIT and ROLLBACK reach StarRocks. DDL commits first, and statements StarRocks refuses inside a transaction raise before they are sent.
  - Each `ALTER TABLE`, `CREATE INDEX` and `DROP INDEX` construct waits for the table's running `COLUMN`, `OPTIMIZE` or `ROLLUP` job, for at most `starrocks_schema_change_timeout` seconds (default 300), and then raises `SchemaChangeTimeoutError`. After a `CREATE INDEX` or `DROP INDEX`, which has already run, a timeout of the wait for its own job only warns. Such a statement sent as text, as through `op.execute()`, doesn't wait. `starrocks_wait_for_schema_change=False` turns the wait off. Both are `create_engine()` options, which the URL's query string can also give. They are separate from the Alembic `context.configure()` options of the same names, which are off by default and wait after each of a migration's own statements.
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
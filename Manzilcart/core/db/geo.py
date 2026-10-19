"""
Database-level geographic integrity.

The rule
--------
A listing/deal/user stores both ``emirate`` and ``service_area``. The service area
must belong to that emirate. Django cannot model this as a constraint: it needs a
composite foreign key, because ``ForeignKey`` to a composite primary key is not
supported by Django (6.1 raises an explicit error).

The fix
-------
PostgreSQL *can* express it. We declare a unique index on
``(id, emirate_id)`` of ``locations_servicearea`` (see ``ServiceArea.Meta``) and add
a composite foreign key from ``(service_area_id, emirate_id)`` of each scoped table
to that index. A mismatched pair is then rejected by the database, not merely by
application code.

Usage in a migration::

    from core.db.geo import geo_fk_sql

    class Migration(migrations.Migration):
        operations = [
            migrations.RunSQL(
                *geo_fk_sql("catalog_listing"),
                state_operations=[],
            )
        ]

Tables added later must add their own constraint in their own migration.
"""

from __future__ import annotations

SERVICE_AREA_TABLE = "locations_servicearea"

# Referenced column list must match the order of the unique index
# ``(id, emirate_id)`` declared on ServiceArea.Meta.constraints.
REFERENCED_COLUMNS = "(id, emirate_id)"


def _constraint_name(table: str) -> str:
    return f"{table}_service_area_emirate_fk"


def geo_fk_sql(table: str) -> tuple[str, str]:
    """
    Return ``(forward_sql, reverse_sql)`` for adding the composite geographic FK.

    The referencing column order is ``(service_area_id, emirate_id)`` so it lines up
    positionally with the referenced ``(id, emirate_id)``.
    """
    name = _constraint_name(table)
    forward = (
        f"ALTER TABLE {table} "
        f"ADD CONSTRAINT {name} "
        f"FOREIGN KEY (service_area_id, emirate_id) "
        f"REFERENCES {SERVICE_AREA_TABLE} {REFERENCED_COLUMNS} "
        f"ON UPDATE RESTRICT ON DELETE RESTRICT"
    )
    reverse = f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"
    return forward, reverse


def drop_geo_fk_sql(table: str) -> str:
    """SQL to remove a previously added composite geographic FK."""
    return f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {_constraint_name(table)}"


def _is_postgresql(schema_editor) -> bool:
    return schema_editor.connection.vendor == "postgresql"


def apply_geo_fk(schema_editor, table: str) -> None:
    """
    Add the composite FK from a migration.

    No-op on non-PostgreSQL databases (the SQLite development fallback cannot express
    it). Production *must* be PostgreSQL, where the guarantee is real.
    """
    if not _is_postgresql(schema_editor):
        return
    forward, _reverse = geo_fk_sql(table)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(forward)


def remove_geo_fk(schema_editor, table: str) -> None:
    """Drop the composite FK from a migration (no-op off PostgreSQL)."""
    if not _is_postgresql(schema_editor):
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(drop_geo_fk_sql(table))

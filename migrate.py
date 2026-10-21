"""Mini-Migration: ergaenzt fehlende Spalten in bestehenden Tabellen.

db.create_all() legt nur fehlende TABELLEN an, aber keine neuen SPALTEN in
Tabellen, die schon existieren (z. B. auf deiner Render-Datenbank). Diese
Funktion vergleicht Modell und Datenbank und fuehrt bei Bedarf
ALTER TABLE ... ADD COLUMN aus. Sie loescht und veraendert nie etwas.
Laeuft beim App-Start und ist beliebig oft wiederholbar (idempotent).
"""
import logging

import sqlalchemy as sa

log = logging.getLogger(__name__)


def _default_sql(column, dialect):
    default = column.server_default
    if default is None:
        return None
    arg = getattr(default, "arg", None)
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    if arg is None:
        return None
    return str(arg.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def ensure_columns(db):
    engine = db.engine
    dialect = engine.dialect
    inspector = sa.inspect(engine)
    added = []

    with engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # frische Tabelle: create_all() hat sie komplett angelegt
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue

                col_type = column.type.compile(dialect=dialect)
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'

                default_sql = _default_sql(column, dialect)
                if default_sql is not None:
                    ddl += f" DEFAULT {default_sql}"
                if not column.nullable and default_sql is not None:
                    ddl += " NOT NULL"

                for fk in column.foreign_keys:
                    target = fk.column
                    ddl += f' REFERENCES "{target.table.name}" ("{target.name}")'

                conn.execute(sa.text(ddl))
                added.append(f"{table.name}.{column.name}")

    if added:
        log.warning("Migration: neue Spalten angelegt: %s", ", ".join(added))
    return added

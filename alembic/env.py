import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from schemas.database.order_db import Base as OrderBase
from schemas.database.driver_location_db import Base as DriverLocationBase

# Merge metadata from all models
from sqlalchemy import MetaData
combined_metadata = MetaData()
for table in OrderBase.metadata.tables.values():
    table.to_metadata(combined_metadata)
for table in DriverLocationBase.metadata.tables.values():
    table.to_metadata(combined_metadata)

load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use the direct (non-pooler) Neon endpoint for migrations.
# The pooler (PgBouncer transaction mode) silently drops DDL.
_db_url = os.getenv("DATABASE_URL", "")
_db_url = _db_url.replace("-pooler.", ".")
config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = combined_metadata

# Only manage tables that are explicitly declared in this service's metadata.
# This prevents autogenerate from touching tables owned by other services
# sharing the same Neon database.
_MANAGED_TABLES = {t.name for t in target_metadata.sorted_tables}


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in _MANAGED_TABLES
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

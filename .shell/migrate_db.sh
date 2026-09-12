#!/bin/bash

## Apply SQL migrations to Moonraker's database with detailed output
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


MIGRATION_DIR="/opt/config/mod/sql"
DATABASE_PATH="/opt/config/mod_data/database/moonraker-sql.db"
LAST_MIGRATION_FILE="/opt/config/mod/sql/version"


database_ready() {
    [ -f "$DATABASE_PATH" ] && sqlite3 "$DATABASE_PATH" \
        "SELECT 1 FROM namespace_store LIMIT 1;" >/dev/null 2>&1
}

get_last_migration() {
    if [ -f "$LAST_MIGRATION_FILE" ]; then
        cat "$LAST_MIGRATION_FILE"
    else
        echo "00000"
    fi
}

apply_migrations() {
    if ! database_ready; then
        echo "Error: Moonraker database is not ready. Skipping migrations."
        return 0
    fi

    echo "Fetching last migration version..."
    last_migration=$(get_last_migration)
    migrations=($(ls $MIGRATION_DIR/*.sql | sort))
    
    echo "Current database version: ${last_migration}"
    echo "Total migrations found: ${#migrations[@]}"
    
    migration_applied=0
    for migration in "${migrations[@]}"; do
        migration_file=$(basename "$migration")
        migration_name="${migration_file%%.*}"  # Remove .sql extension
        migration_number="${migration_name%%-*}"  # Get the migration number
        
        if [[ "$migration_number" > "$last_migration" ]]; then
            echo "Applying migration: $migration_file"
            
            if sqlite3 "$DATABASE_PATH" < "$migration"; then
                echo "$migration_number" > "$LAST_MIGRATION_FILE"
                sync
                migration_applied=1
            else
                echo "Failed to apply migration: $migration_file. Check the SQL script or database file."
                exit 2
            fi
        fi
    done

    if [ "$migration_applied" -eq 0 ]; then
        echo "Database already up to date"
    else
        sync
        echo "All migrations have been processed"
    fi
}

if [ "${1-}" = "--check" ]; then
    database_ready
    exit $?
fi

apply_migrations

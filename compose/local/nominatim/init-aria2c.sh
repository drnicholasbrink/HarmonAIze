#!/bin/bash


set -euo pipefail

OSMFILE=/nominatim/data.osm.pbf
CURL=("curl" "-L" "-A" "${USER_AGENT:-HarmonAIze-Nominatim/1.0}" "--fail-with-body")

# Build secure SCP command array 
if [ -n "${SCP_PASSWORD:-}" ] && [ -n "${SCP_HOST:-}" ] && [ -n "${SCP_USER:-}" ]; then
    SCP_CMD=("sshpass" "-p" "${SCP_PASSWORD}" "scp" "-o" "StrictHostKeyChecking=no" "${SCP_USER}@${SCP_HOST}")
else
    SCP_CMD=()
fi

if [ -z "${THREADS:-}" ]; then
    THREADS=8
fi

# Import Wikipedia importance data if enabled
if [ "${IMPORT_WIKIPEDIA:-}" = "true" ]; then
    if [ -f "${IMPORT_WIKIPEDIA}" ]; then
        mv "${IMPORT_WIKIPEDIA}" /nominatim/wikimedia-importance.sql.gz
    else
        curl -s -L https://nominatim.org/data/wikimedia-importance.sql.gz -o /nominatim/wikimedia-importance.sql.gz
    fi
fi

# Import GB postcode data if enabled
if [ "${IMPORT_GB_POSTCODES:-}" = "true" ]; then
    if [ -f "${IMPORT_GB_POSTCODES}" ]; then
        mv "${IMPORT_GB_POSTCODES}" /nominatim/gb_postcodes.csv.gz
    else
        curl -s -L https://www.getthedata.com/downloads/open_postcode_geo.csv.zip -o /tmp/gb_postcodes.zip
        unzip -q /tmp/gb_postcodes.zip -d /tmp/
        gzip /tmp/open_postcode_geo.csv
        mv /tmp/open_postcode_geo.csv.gz /nominatim/gb_postcodes.csv.gz
    fi
fi

# Import US postcode data if enabled
if [ "${IMPORT_US_POSTCODES:-}" = "true" ]; then
    if [ -f "${IMPORT_US_POSTCODES}" ]; then
        mv "${IMPORT_US_POSTCODES}" /nominatim/us_postcodes.csv.gz
    else
        curl -s -L https://download.geonames.org/export/zip/US.zip -o /tmp/us_postcodes.zip
        unzip -q /tmp/us_postcodes.zip -d /tmp/
        gzip /tmp/US.txt
        mv /tmp/US.txt.gz /nominatim/us_postcodes.csv.gz
    fi
fi

# Import Tiger address data if enabled
if [ "${IMPORT_TIGER_ADDRESSES:-}" = "true" ]; then
    if [ -f "${IMPORT_TIGER_ADDRESSES}" ]; then
        mv "${IMPORT_TIGER_ADDRESSES}" /nominatim/tiger-data
    else
        nominatim add-data --tiger-data /nominatim/tiger-data > /dev/null 2>&1
    fi
fi

# Download OSM data using aria2c for faster parallel downloads
if [ "${PBF_URL:-}" != "" ]; then
    aria2c \
        --max-connection-per-server=16 \
        --split=16 \
        --max-concurrent-downloads=1 \
        --continue=true \
        --allow-overwrite=true \
        --auto-file-renaming=false \
        --dir=/nominatim \
        --out=data.osm.pbf \
        --user-agent="mediagis/nominatim-docker:4.4.1-aria2c" \
        --retry-wait=3 \
        --max-tries=5 \
        --timeout=60 \
        --connect-timeout=30 \
        --quiet \
        "$PBF_URL"
    
    if [ $? -ne 0 ]; then
        "${CURL[@]}" "$PBF_URL" -s -C - --create-dirs -o "$OSMFILE"
    fi
elif [ "${PBF_PATH:-}" != "" ]; then
    ln -s "$PBF_PATH" "$OSMFILE"
elif [ "${SCP_SOURCE_PATH:-}" != "" ] && [ ${#SCP_CMD[@]} -gt 0 ]; then
    "${SCP_CMD[@]}" "$SCP_SOURCE_PATH" "$OSMFILE" > /dev/null 2>&1
else
    exit 1
fi

# Verify OSM file exists
if [ ! -f "$OSMFILE" ]; then
    exit 1
fi

# Silent file integrity check
if command -v osmium &> /dev/null; then
    osmium fileinfo "$OSMFILE" > /dev/null 2>&1
fi

# Prepare import arguments
IMPORT_ARGS=(
    --osm-file "$OSMFILE"
    --threads "$THREADS"
    --project-dir /nominatim
)

# Add optional import style
if [ "${IMPORT_STYLE:-}" != "" ]; then
    IMPORT_ARGS+=(--osm2pgsql-style "$IMPORT_STYLE")
fi

# Add reverse-only mode if specified
if [ "${REVERSE_ONLY:-}" = "true" ]; then
    IMPORT_ARGS+=(--reverse-only)
fi

# Add Wikipedia importance data if available
if [ -f "/nominatim/wikimedia-importance.sql.gz" ]; then
    IMPORT_ARGS+=(--import-file /nominatim/wikimedia-importance.sql.gz)
fi

# Collect all postcode files into a single argument
POSTCODE_FILES=()
if [ -f "/nominatim/gb_postcodes.csv.gz" ]; then
    POSTCODE_FILES+=("/nominatim/gb_postcodes.csv.gz")
fi
if [ -f "/nominatim/us_postcodes.csv.gz" ]; then
    POSTCODE_FILES+=("/nominatim/us_postcodes.csv.gz")
fi

# Add postcodes as a single argument if any exist
if [ ${#POSTCODE_FILES[@]} -gt 0 ]; then
    IMPORT_ARGS+=(--postcodes-file "${POSTCODE_FILES[0]}")
   
fi

# Execute Nominatim import process
if id -u nominatim >/dev/null 2>&1; then
    su - nominatim -c "nominatim import $(printf '%q ' "${IMPORT_ARGS[@]}")" > /dev/null 2>&1
else
    # Run as current user if nominatim user doesn't exist
    nominatim import "${IMPORT_ARGS[@]}" > /dev/null 2>&1
fi

IMPORT_EXIT_CODE=$?

if [ $IMPORT_EXIT_CODE -eq 0 ]; then
    # Add Tiger addresses if enabled and data exists
    if [ "${IMPORT_TIGER_ADDRESSES:-}" = "true" ] && [ -d "/nominatim/tiger-data" ]; then
        if id -u nominatim >/dev/null 2>&1; then
            su - nominatim -c "nominatim add-data --tiger-data /nominatim/tiger-data" > /dev/null 2>&1
        else
            nominatim add-data --tiger-data /nominatim/tiger-data > /dev/null 2>&1
        fi
    fi
    
    # Create import completion marker
    touch /var/lib/postgresql/14/main/import-finished
    
    # Clean up temporary files to save disk space
    if [ "${PBF_URL:-}" != "" ]; then
        rm -f "$OSMFILE"
    fi
    
    # Remove optional data files
    rm -f /nominatim/wikimedia-importance.sql.gz
    rm -f /nominatim/gb_postcodes.csv.gz
    rm -f /nominatim/us_postcodes.csv.gz
    rm -rf /nominatim/tiger-data
else
    exit $IMPORT_EXIT_CODE
fi
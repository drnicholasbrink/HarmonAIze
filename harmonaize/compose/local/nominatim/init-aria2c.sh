#!/bin/bash

# Custom init script using aria2c for faster downloads
# Based on the original mediagis/nominatim init.sh

set -euo pipefail

OSMFILE=/nominatim/data.osm.pbf
CURL=("curl" "-L" "-A" "${USER_AGENT}" "--fail-with-body")
SCP='sshpass -p DMg5bmLPY7npHL2Q scp -o StrictHostKeyChecking=no u355874-sub1@u355874-sub1.your-storagebox.de'

if [ -z "${THREADS:-}" ]; then
    THREADS=8
fi

if [ "${REVERSE_ONLY:-}" = "true" ]; then
    echo "Reverse only mode"
fi

# Optional Wikipedia importance import
if [ "${IMPORT_WIKIPEDIA:-}" = "true" ]; then
    if [ -f "${IMPORT_WIKIPEDIA}" ]; then
        echo "Downloading optional Wikipedia importance import"
        mv "${IMPORT_WIKIPEDIA}" /nominatim/wikimedia-importance.sql.gz
    else
        echo "Downloading optional Wikipedia importance import"
        curl -L https://nominatim.org/data/wikimedia-importance.sql.gz -o /nominatim/wikimedia-importance.sql.gz
    fi
else
    echo "Skipping optional Wikipedia importance import"
fi

# Optional GB postcodes import
if [ "${IMPORT_GB_POSTCODES:-}" = "true" ]; then
    if [ -f "${IMPORT_GB_POSTCODES}" ]; then
        echo "Using local GB postcode data"
        mv "${IMPORT_GB_POSTCODES}" /nominatim/gb_postcodes.csv.gz
    else
        echo "Downloading optional GB postcode data"
        curl -L https://www.getthedata.com/downloads/open_postcode_geo.csv.zip -o /tmp/gb_postcodes.zip
        unzip -o /tmp/gb_postcodes.zip -d /tmp/
        gzip /tmp/open_postcode_geo.csv
        mv /tmp/open_postcode_geo.csv.gz /nominatim/gb_postcodes.csv.gz
    fi
else
    echo "Skipping optional GB postcode import"
fi

# Optional US postcodes import
if [ "${IMPORT_US_POSTCODES:-}" = "true" ]; then
    if [ -f "${IMPORT_US_POSTCODES}" ]; then
        echo "Using local US postcode data"
        mv "${IMPORT_US_POSTCODES}" /nominatim/us_postcodes.csv.gz
    else
        echo "Downloading optional US postcode data"
        curl -L https://download.geonames.org/export/zip/US.zip -o /tmp/us_postcodes.zip
        unzip -o /tmp/us_postcodes.zip -d /tmp/
        gzip /tmp/US.txt
        mv /tmp/US.txt.gz /nominatim/us_postcodes.csv.gz
    fi
else
    echo "Skipping optional US postcode import"
fi

# Optional Tiger addresses import
if [ "${IMPORT_TIGER_ADDRESSES:-}" = "true" ]; then
    if [ -f "${IMPORT_TIGER_ADDRESSES}" ]; then
        echo "Using local Tiger address data"
        mv "${IMPORT_TIGER_ADDRESSES}" /nominatim/tiger
    else
        echo "Downloading optional Tiger address data"
        nominatim add-data --tiger-data /nominatim/tiger-data
    fi
else
    echo "Skipping optional Tiger addresses import"
fi

# Download OSM data using aria2c for faster downloads
if [ "${PBF_URL:-}" != "" ]; then
    echo "Downloading OSM extract from $PBF_URL using aria2c (16 parallel connections)"
    
    # Use aria2c with multiple connections for faster download
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
        "$PBF_URL"
    
    if [ $? -ne 0 ]; then
        echo "Failed to download OSM data with aria2c, falling back to curl"
        "${CURL[@]}" "$PBF_URL" -C - --create-dirs -o "$OSMFILE"
    fi
elif [ "${PBF_PATH:-}" != "" ]; then
    echo "Using local PBF file: $PBF_PATH"
    ln -s "$PBF_PATH" "$OSMFILE"
elif [ "${SCP_SOURCE_PATH:-}" != "" ]; then
    echo "Downloading OSM extract from SCP server"
    eval "$SCP $SCP_SOURCE_PATH $OSMFILE"
else
    echo "No PBF_URL, PBF_PATH, or SCP_SOURCE_PATH provided"
    exit 1
fi

if [ ! -f "$OSMFILE" ]; then
    echo "ERROR: No OSM file found at $OSMFILE"
    exit 1
fi

echo "OSM file downloaded successfully!"
echo "OSM file size: $(du -h $OSMFILE | cut -f1)"

# Verify file integrity if osmium is available
if command -v osmium &> /dev/null; then
    echo "Verifying OSM file integrity..."
    if ! osmium fileinfo "$OSMFILE" > /dev/null 2>&1; then
        echo "WARNING: OSM file appears to be corrupted, but continuing import..."
    else
        echo "OSM file integrity check passed"
    fi
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

# Add GB postcode data if available
if [ -f "/nominatim/gb_postcodes.csv.gz" ]; then
    IMPORT_ARGS+=(--postcodes-file /nominatim/gb_postcodes.csv.gz)
fi

# Add US postcode data if available
if [ -f "/nominatim/us_postcodes.csv.gz" ]; then
    IMPORT_ARGS+=(--postcodes-file /nominatim/us_postcodes.csv.gz)
fi

# Import data into Nominatim
echo "Starting Nominatim import process..."
echo "This will take 2-4 hours for the full Africa dataset..."

# Run the import
sudo -u nominatim nominatim import "${IMPORT_ARGS[@]}" 2>&1

IMPORT_EXIT_CODE=$?

if [ $IMPORT_EXIT_CODE -eq 0 ]; then
    echo "Import completed successfully!"
    
    # Add Tiger addresses if specified
    if [ "${IMPORT_TIGER_ADDRESSES:-}" = "true" ] && [ -d "/nominatim/tiger" ]; then
        echo "Adding Tiger address data..."
        sudo -u nominatim nominatim add-data --tiger-data /nominatim/tiger
    fi
    
    # Create import finished marker
    touch /var/lib/postgresql/14/main/import-finished
    
    # Clean up downloaded files to save space
    if [ "${PBF_URL:-}" != "" ]; then
        echo "Cleaning up downloaded OSM file to save disk space"
        rm -f "$OSMFILE"
    fi
    
    # Clean up optional data files
    rm -f /nominatim/wikimedia-importance.sql.gz
    rm -f /nominatim/gb_postcodes.csv.gz
    rm -f /nominatim/us_postcodes.csv.gz
    rm -rf /nominatim/tiger
    
    echo "Nominatim initialization complete"
else
    echo "ERROR: Import failed with exit code $IMPORT_EXIT_CODE"
    exit $IMPORT_EXIT_CODE
fi
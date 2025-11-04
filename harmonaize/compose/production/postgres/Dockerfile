FROM docker.io/postgres:14

# Install dependencies for pgvector
RUN apt-get update \
    && apt-get install -y \
        postgresql-14-pgvector \
    && rm -rf /var/lib/apt/lists/*

# Copy initialization script
COPY ./compose/production/postgres/init-db.sh /docker-entrypoint-initdb.d/
RUN chmod +x /docker-entrypoint-initdb.d/init-db.sh

COPY ./compose/production/postgres/maintenance /usr/local/bin/maintenance
RUN chmod +x /usr/local/bin/maintenance/*
RUN mv /usr/local/bin/maintenance/* /usr/local/bin \
    && rmdir /usr/local/bin/maintenance

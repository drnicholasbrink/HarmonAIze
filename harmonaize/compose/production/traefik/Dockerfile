FROM docker.io/traefik:v3.7.19

# Run Traefik as an unprivileged user. Binding ports 80/443 still works because
# Docker (20.10+) sets net.ipv4.ip_unprivileged_port_start=0 inside containers.
#
# Upgrading an existing deployment: a production_traefik volume created by an
# older image holds a root-owned acme.json, which this user cannot read. Fix it
# once with:
#   docker compose -f docker-compose.production.yml run --rm --no-deps --user root \
#     --entrypoint chown traefik -R 10001:10001 /etc/traefik/acme
RUN addgroup -S -g 10001 traefik \
  && adduser -S -D -H -u 10001 -G traefik traefik \
  && mkdir -p /etc/traefik/acme \
  && touch /etc/traefik/acme/acme.json \
  && chmod 600 /etc/traefik/acme/acme.json \
  && chown -R traefik:traefik /etc/traefik/acme
COPY ./compose/production/traefik/traefik.yml /etc/traefik

USER traefik

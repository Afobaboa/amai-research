#!/bin/sh
set -eu

# Migrations are explicit: run `docker compose run --rm web python manage.py migrate`.
python manage.py collectstatic --noinput
exec "$@"

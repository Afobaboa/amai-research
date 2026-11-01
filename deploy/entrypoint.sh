#!/bin/sh
set -eu

# Compose waits for the initialize service before starting the application.
python manage.py collectstatic --noinput
exec "$@"

"""Application settings; production requires explicit secrets and PostgreSQL."""
import os
from urllib.parse import unquote, urlparse, parse_qs

from django.core.exceptions import ImproperlyConfigured
from django.contrib.auth.hashers import make_password

from .base import *  # noqa: F403

DEBUG = os.getenv('DEBUG', 'true').lower() in ('true', '1', 'yes')
DEMO_MODE = os.getenv('DEMO_MODE', 'false').lower() in ('true', '1', 'yes')
SECRET_KEY = os.getenv('SECRET_KEY', 'local-demo-only-never-use-in-production')
SITE_PASSWORD_HASH = os.getenv('SITE_PASSWORD_HASH', '')
if not SITE_PASSWORD_HASH and DEBUG:
    SITE_PASSWORD_HASH = make_password('lims-demo')
SHARED_PASSWORD_HASH = SITE_PASSWORD_HASH
if not DEBUG and (not os.getenv('SECRET_KEY') or not SITE_PASSWORD_HASH):
    raise ImproperlyConfigured('Production requires SECRET_KEY and SITE_PASSWORD_HASH.')
if not DEBUG and DEMO_MODE:
    raise ImproperlyConfigured('DEMO_MODE must be false in production.')
ALLOWED_HOSTS = [v.strip() for v in os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost,[::1]').split(',') if v.strip()]
CSRF_TRUSTED_ORIGINS = [v.strip() for v in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if v.strip()]
database_url = os.getenv('DATABASE_URL', '')
if database_url:
    parsed = urlparse(database_url)
    if parsed.scheme not in ('postgres', 'postgresql'):
        raise ImproperlyConfigured('DATABASE_URL must use PostgreSQL.')
    options = {key: values[-1] for key, values in parse_qs(parsed.query).items() if key in ('sslmode', 'sslrootcert')}
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': unquote(parsed.path.lstrip('/')), 'USER': unquote(parsed.username or ''), 'PASSWORD': unquote(parsed.password or ''), 'HOST': parsed.hostname, 'PORT': parsed.port or 5432, 'CONN_MAX_AGE': 60, 'OPTIONS': options}}
else:
    if not DEBUG:
        raise ImproperlyConfigured('Production requires DATABASE_URL for PostgreSQL.')
    (BASE_DIR / 'var').mkdir(exist_ok=True)
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.getenv('SQLITE_PATH', str(BASE_DIR / 'var' / 'lims.sqlite3')), 'OPTIONS': {'timeout': 20}}}
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

"""Explicit offline application profile, independent of production environment."""
import os
from pathlib import Path

from django.contrib.auth.hashers import make_password

from .base import *  # noqa: F403

DEBUG = True
DEMO_MODE = True
SECRET_KEY = 'local-demo-only-never-use-in-production'
SITE_PASSWORD_HASH = make_password('lims-demo')
SHARED_PASSWORD_HASH = SITE_PASSWORD_HASH
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '[::1]']
CSRF_TRUSTED_ORIGINS = []
local_database = Path(os.environ.get('LIMS_LOCAL_DB', str(BASE_DIR / 'var' / 'local.sqlite3'))).expanduser()
if not local_database.is_absolute():
    local_database = BASE_DIR / local_database
local_database.parent.mkdir(parents=True, exist_ok=True)
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(local_database), 'OPTIONS': {'timeout': 20}}}
MEDIA_ROOT = Path(os.environ.get('LIMS_LOCAL_MEDIA', str(BASE_DIR / 'media'))).expanduser()
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

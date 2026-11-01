"""Shared application settings; runtime profiles choose credentials and storage."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'lab']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages', 'lab.context_processors.workspace']}}]
WSGI_APPLICATION = 'config.wsgi.application'
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'}]
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 60 * 60 * 12
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
X_FRAME_OPTIONS = 'DENY'

# Shared by local and production profiles. These values never enter templates.
CLOUD_RU_API_KEY = os.getenv('CLOUD_RU_API_KEY', '').strip()
CLOUD_RU_MODEL = os.getenv('CLOUD_RU_MODEL', 'deepseek-ai/DeepSeek-V4-Pro').strip()
CLOUD_RU_BASE_URL = os.getenv('CLOUD_RU_BASE_URL', 'https://foundation-models.api.cloud.ru/v1').rstrip('/')
CLOUD_RU_TIMEOUT = os.getenv('CLOUD_RU_TIMEOUT', '300')
ASSISTANT_MAX_TOOL_ROUNDS = os.getenv('ASSISTANT_MAX_TOOL_ROUNDS', '5')

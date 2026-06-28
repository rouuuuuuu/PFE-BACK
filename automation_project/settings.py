"""
Django settings for automation_project project.
"""

from pathlib import Path
from celery.schedules import crontab
import sys
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-a%5#ya5@pdz&0mbv52v4r)i!iz*-=k+pkw1@=zn*i%^aw0axe8'

DEBUG = True

ALLOWED_HOSTS = ['*']

# ── Applications ──
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'rest_framework.authtoken',
    'django_filters',
    'corsheaders',
    # DRS 010 apps
    'backhaul',
    'b2b',
    'devices',
    'services',
    'provisioning',
    'monitoring',
    'reporting',
    'ai',
    'inventory',
    'accounts',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

CORS_ALLOWED_ORIGINS = [
    'http://localhost:4200',
]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = True

# ── Celery + Redis ──
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'

CELERY_BEAT_SCHEDULE = {
    'generate-monthly-report-on-the-1st': {
        'task': 'reporting.tasks.generate_monthly_network_report',
        'schedule': crontab(minute=5, hour=0, day_of_month=1),
    },
}

# ── Django REST Framework ──
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
    ],
}

# ── Custom User Model ──
AUTH_USER_MODEL = 'accounts.CustomUser'

ROOT_URLCONF = 'automation_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'automation_project.wsgi.application'

# ── Database ──
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'drs010_db',
        'USER': 'drs010_user',
        'PASSWORD': 'drs010pass',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}

if 'test' in sys.argv:
    DATABASES['default']['TEST'] = {
        'NAME': 'test_drs010_fresh',
    }

# ── Password Validation ──
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ── Email (prints to terminal in dev) ──
# ── Email ──
EMAIL_BACKEND       = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST          = 'smtp.gmail.com'
EMAIL_PORT          = 587
EMAIL_USE_TLS       = True
EMAIL_HOST_USER     = 'rrrooouuuaaa401@gmail.com'
EMAIL_HOST_PASSWORD = 'jdjvlxlgbjhyxbmg'

# ── Cache (Redis, used for password reset codes) ──
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': 'redis://localhost:6379/1',
    }
}

# ── Internationalization ──
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ── Media ──
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# ── Static ──
STATIC_URL = 'static/'

# ── Default PK ──
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Configuration OpenRouter pour l'analyse d'alarmes / tâches IA
OPENROUTER_API_KEY = "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"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

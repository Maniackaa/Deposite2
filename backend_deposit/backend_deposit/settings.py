import os
from pathlib import Path

import pytz
from celery.schedules import crontab
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv('SECRET_KEY')
DEBUG = (os.getenv('DEBUG', 'False').lower() == 'true')

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS').split(',')
CSRF_TRUSTED_ORIGINS = os.getenv('CSRF_TRUSTED_ORIGINS').split(',')

MY_APPS = [
    'users.apps.UsersConfig',
    'deposit.apps.DepositConfig',
    'ocr.apps.OcrConfig',
    'crispy_bootstrap4',
    'rangefilter',
    'spurl',
    'mathfilters',
    'celery',
    'django_celery_beat',
]
CRISPY_TEMPLATE_PACK = 'bootstrap4'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'sorl.thumbnail',
    'colorfield',
    'debug_toolbar',
] + MY_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'debug_toolbar.middleware.DebugToolbarMiddleware'
]

INTERNAL_IPS = ['127.0.0.1', 'localhost']

ROOT_URLCONF = 'backend_deposit.urls'

TEMPLATES_DIR = BASE_DIR / 'templates'
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [TEMPLATES_DIR],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'backend_deposit.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB', 'django'),
        'USER': os.getenv('POSTGRES_USER', 'django'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', ''),
        'PORT': os.getenv('DB_PORT', 5432)
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'ru-RU'

# TIME_ZONE = 'UTC'
TIME_ZONE = os.getenv('TIMEZONE')
# TZ = pytz.timezone(TIME_ZONE)

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'collected_static'

STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media/'


# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'users.User'
LOGIN_URL = 'users:login'
LOGIN_REDIRECT_URL = 'deposit:index'
LOGOUT_REDIRECT_URL = 'users:login'

# EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
EMAIL_FILE_PATH = MEDIA_ROOT / 'email'

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],

    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.BasicAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],

    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],

    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser'
    ],

    'TEST_REQUEST_DEFAULT_FORMAT': 'json',
}

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default_formatter': {
            'format': '[%(asctime)s] #%(levelname)-8s %(filename)s:%(lineno)d %(module)s/%(funcName)s\n%(message)s',
        },
    },
    'handlers': {
        # 'file': {
        #     'level': 'DEBUG',
        #     'class': 'logging.FileHandler',
        #     'filename': 'logs/log.log',
        #     'formatter': 'default_formatter',
        #
        # },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'default_formatter',
        },
        'rotate': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename':'logs/deposite_rotate.log',
            'backupCount': 10,
            'maxBytes': 100 * 1024 * 1024,
            'mode': 'a',
            'encoding': 'UTF-8',
            'formatter': 'default_formatter',
            'level': 'DEBUG',
        },
        'celery_handler': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename':  'logs/celery_tasks.log',
            'backupCount': 10,
            'maxBytes': 100 * 1024 * 1024,
            'mode': 'a',
            'encoding': 'UTF-8',
            'formatter': 'default_formatter',
            'level': 'DEBUG',
        },
        'errors': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'logs/errors.log',
            'backupCount': 10,
            'maxBytes': 10 * 1024 * 1024,
            'mode': 'a',
            'encoding': 'UTF-8',
            'formatter': 'default_formatter',
            'level': 'ERROR',
        },
        'ocr_rotate': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'logs/ocr_rotate.log',
            'backupCount': 10,
            'maxBytes': 100 * 1024 * 1024,
            'mode': 'a',
            'encoding': 'UTF-8',
            'formatter': 'default_formatter',
            'level': 'DEBUG',
        },
        'django_rotate': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': 'logs/django_rotate.log',
            'backupCount': 10,
            'maxBytes': 100 * 1024 * 1024,
            'mode': 'a',
            'encoding': 'UTF-8',
            'formatter': 'default_formatter',
            'level': 'WARNING',
        }
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'django_rotate'],
            'level': 'WARNING',
            'propagate': True
        },
        'deposit': {
            'handlers': ['console', 'rotate', 'errors'],
            'level': 'DEBUG',
            'propagate': True
        },
        'ocr': {
            'handlers': ['console', 'ocr_rotate', 'errors'],
            'level': 'DEBUG',
            'propagate': True
        },
        'celery': {
            'handlers': ['celery_handler', 'console'],
            'level': 'DEBUG',
            'propagate': True,
        },
    }
}

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = os.getenv('ADMIN_IDS').split(',')
ALARM_IDS = os.getenv('ALARM_IDS').split(',')
PAGINATE = 100
USE_THOUSAND_SEPARATOR = True

# Celery settings
# REDIS_PASSWORD = os.getenv('REDIS_PASSWORD')
# REDIS_URL = 'redis://redis:6739/0'
CELERY_BROKER_URL = 'redis://redis:6379'
CELERY_RESULT_BACKEND = 'redis://redis:6379'
CELERY_TIMEZONE = TIME_ZONE
CELERYD_LOG_FILE = os.path.join(BASE_DIR, "logs", "celery_work.log")
CELERYBEAT_LOG_FILE = os.path.join(BASE_DIR, "logs", "celery_beat.log")
CELERYD_HIJACK_ROOT_LOGGER = False
# CELERY_BEAT_SCHEDULE = {
#     "check_macros": {
#         "task": "deposit.tasks.check_macros",
#         # "schedule": crontab(minute="*/1"),
#         "schedule": 10,
#     },
# }
REMOTE_SERVER = os.getenv('REMOTE_SERVER')

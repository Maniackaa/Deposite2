import os
from pathlib import Path

import pytz
import structlog
from celery.schedules import crontab
from dotenv import load_dotenv
from structlog.typing import WrappedLogger, EventDict

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
    'payment.apps.PaymentConfig',
    'api.apps.ApiConfig',
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
    'crispy_bootstrap4',
    'rangefilter',
    'spurl',
    'mathfilters',
    'celery',
    'django_celery_beat',
    'django_structlog',
    'django_filters',
    'corsheaders',
    'django_better_admin_arrayfield',
] + MY_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'debug_toolbar.middleware.DebugToolbarMiddleware',
    'django_structlog.middlewares.RequestMiddleware',
    'better_exceptions.integrations.django.BetterExceptionsMiddleware',
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
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "json_formatter": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.processors.JSONRenderer(),
        },
        "plain_console": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.dev.ConsoleRenderer(),
        },
    },
    "handlers": {
        "null": {
            "class": "logging.NullHandler",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain_console",
            # 'filters': ['skip_errors'],
        },
        "json_file": {
            "class": "logging.handlers.WatchedFileHandler",
            "filename": "logs/json.log",
            "formatter": "json_formatter",
        },
        "console_file": {
            "class": "logging.handlers.WatchedFileHandler",
            "filename": "logs/console.log",
            "formatter": "plain_console",
        },
        "root_handler": {
            "class": "logging.handlers.WatchedFileHandler",
            "filename": "logs/root.log",
            "formatter": "plain_console",
        },
    },
    "loggers": {
        "django.request": {
            "handlers": ["null"],
            "propagate": False,
        },
        "deposit": {
            "handlers": ["console", "console_file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "payment": {
            "handlers": ["console", "console_file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "api": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "root": {
            "handlers": [],
            "level": "WARNING",
            "propagate": False,
        },


    },
}


class LogJump:
    def __init__(
            self,
            full_path: bool = False,
    ) -> None:
        self.full_path = full_path

    def __call__(
            self, logger: WrappedLogger, name: str, event_dict: EventDict
    ) -> EventDict:
        if self.full_path:
            file_part = "\n" + event_dict.pop("pathname")
        else:
            file_part = event_dict.pop("filename")
        event_dict["location"] = f'"{file_part}:{event_dict.pop("lineno")}"'

        return event_dict


base_structlog_processors = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.filter_by_level,
    # Perform %-style formatting.
    structlog.stdlib.PositionalArgumentsFormatter(),
    # Add a timestamp in ISO 8601 format.
    structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
    structlog.processors.StackInfoRenderer(),
    # If some value is in bytes, decode it to a unicode str.
    structlog.processors.UnicodeDecoder(),
    # Add callsite parameters.
    structlog.processors.CallsiteParameterAdder(
        {
            structlog.processors.CallsiteParameter.FILENAME,
            structlog.processors.CallsiteParameter.FUNC_NAME,
            structlog.processors.CallsiteParameter.LINENO,
        }
    ),
]

base_structlog_formatter = [structlog.stdlib.ProcessorFormatter.wrap_for_formatter]

structlog.configure(
    processors=base_structlog_processors + base_structlog_formatter,  # type: ignore
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)


os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)


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
# CORS_ALLOWED_ORIGINS = ["http://91.105.172.137", "http://localhost:3000"]
CORS_ALLOWED_ORIGINS = ["http://91.105.172.137", "http://localhost:3000", "https://asu-payme.com"]
# CORS_ALLOW_ALL_ORIGINS = True
# CORS_ALLOW_CREDENTIALS = True
print('DEBUG-', DEBUG)

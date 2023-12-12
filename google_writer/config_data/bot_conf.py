import os
from dataclasses import dataclass
import pytz
from pathlib import Path
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent


"""
format = "%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s"
"""

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,

    'formatters': {
        'default_formatter': {
            # 'format': "%(asctime)s - [%(levelname)8s] - %(name)s - (%(filename)s).%(funcName)s(%(lineno)d) - %(message)s"
            'format': "%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s"
        },
    },

    'handlers': {
        'stream_handler': {
            'class': 'logging.StreamHandler',
            'formatter': 'default_formatter',
        },
        'rotating_file_handler': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': f'{BASE_DIR / "logs" / "bot"}.log',
            'backupCount': 2,
            'maxBytes': 10 * 1024 * 1024,
            'mode': 'a',
            'encoding': 'UTF-8',
            'formatter': 'default_formatter',
        },
        'table1_file_handler': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': f'{BASE_DIR / "logs" / "table1"}.log',
            'backupCount': 2,
            'maxBytes': 10 * 1024 * 1024,
            'mode': 'a',
            'encoding': 'UTF-8',
            'formatter': 'default_formatter',
        },
        'table2_file_handler': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': f'{BASE_DIR / "logs" / "table2"}.log',
            'backupCount': 2,
            'maxBytes': 10 * 1024 * 1024,
            'mode': 'a',
            'encoding': 'UTF-8',
            'formatter': 'default_formatter',
        },
        'errors_file_handler': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': f'{BASE_DIR / "logs" / "errors_bot"}.log',
            'backupCount': 2,
            'maxBytes': 10 * 1024 * 1024,
            'mode': 'a',
            'encoding': 'UTF-8',
            'formatter': 'default_formatter',
        },
    },
    'loggers': {
        'bot_logger': {
            'handlers': ['stream_handler', 'rotating_file_handler'],
            'level': 'DEBUG',
            'propagate': True
        },
        'errors_logger': {
            'handlers': ['stream_handler', 'errors_file_handler'],
            'level': 'DEBUG',
            'propagate': True
        },
        'table1_logger': {
            'handlers': ['stream_handler', 'table1_file_handler'],
            'level': 'DEBUG',
            'propagate': True
        },
        'table2_logger': {
            'handlers': ['stream_handler', 'table2_file_handler'],
            'level': 'DEBUG',
            'propagate': True
        },
    }
}


@dataclass
class PostgresConfig:
    database: str  # Название базы данных
    db_host: str  # URL-адрес базы данных
    db_port: str  # URL-адрес базы данных
    db_user: str  # Username пользователя базы данных
    db_password: str  # Пароль к базе данных


@dataclass
class RedisConfig:
    redis_db_num: str  # Название базы данных
    redis_host: str  # URL-адрес базы данных
    REDIS_PORT: str  # URL-адрес базы данных
    REDIS_PASSWORD: str


@dataclass
class TgBot:
    token: str  # Токен для доступа к телеграм-боту
    admin_ids: list  # Список id администраторов бота
    base_dir = BASE_DIR
    TABLE_1: str
    TABLE_2: str
    TABLE_SHEET_NUM: int
    TIMEZONE: pytz.timezone
    GROUP_ID: str


@dataclass
class Logic:
    pass


@dataclass
class Config:
    tg_bot: TgBot
    db: PostgresConfig
    logic: Logic
    redis_db: RedisConfig


def load_config(path=None) -> Config:
    return Config(tg_bot=TgBot(token=os.getenv('BOT_TOKEN'),
                               admin_ids=list(os.getenv('ADMIN_IDS').split(',')),
                               TABLE_1=os.getenv('TABLE_1'),
                               TABLE_2=os.getenv('TABLE_2'),
                               TABLE_SHEET_NUM=int(os.getenv('TABLE_SHEET_NUM')),
                               TIMEZONE=pytz.timezone(os.getenv('TIMEZONE')),
                               GROUP_ID=os.getenv('GROUP_ID'),
                               ),
                  db=PostgresConfig(database=os.getenv('POSTGRES_DB'),
                                    db_host=os.getenv('DB_HOST'),
                                    db_port=os.getenv('DB_PORT'),
                                    db_user=os.getenv('POSTGRES_USER'),
                                    db_password=os.getenv('POSTGRES_PASSWORD'),
                                    ),
                  redis_db=RedisConfig(
                      redis_db_num=os.getenv('redis_db_num'),
                      redis_host=os.getenv('redis_host'),
                      REDIS_PORT=os.getenv('REDIS_PORT'),
                      REDIS_PASSWORD=os.getenv('REDIS_PASSWORD'),
                  ),
                  logic=Logic(),
                  )


load_dotenv()

conf = load_config()
tz = conf.tg_bot.TIMEZONE
print(conf)


def get_my_loggers():
    import logging.config
    logging.config.dictConfig(LOGGING_CONFIG)
    return logging.getLogger('bot_logger'), logging.getLogger('errors_logger'), logging.getLogger('table1_logger'),  logging.getLogger('table2_logger')

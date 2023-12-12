import asyncio
import datetime
import time

import gspread
import gspread_asyncio
from gspread.utils import rowcol_to_a1

from config_data.bot_conf import BASE_DIR, conf, get_my_loggers
from google.oauth2.service_account import Credentials

logger, err_log, logger1, logger2 = get_my_loggers()


def get_creds():
    # To obtain a service account JSON file, follow these steps:
    # https://gspread.readthedocs.io/en/latest/oauth2.html#for-bots-using-service-account
    json_file = BASE_DIR / 'credentials.json'
    creds = Credentials.from_service_account_file(json_file)
    scoped = creds.with_scopes([
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    return scoped


async def load_range_values(url=conf.tg_bot.TABLE_1, sheets_num=0, diap='А:А'):
    logger1.debug(f'Читаем таблицу {url}, лист {sheets_num}, диапазон: {diap}')
    agcm = gspread_asyncio.AsyncioGspreadClientManager(get_creds)
    agc = await agcm.authorize()
    sheet = await agc.open_by_url(url)
    table = await sheet.get_worksheet(sheets_num)
    values = await table.get_values(diap)
    print(values)
    return values


async def write_to_table(rows: list[list], start_row=1, url=conf.tg_bot.TABLE_1, sheets_num=0, delta_col=0):
    """
    Функция отправки анкеты CSI через бота
    :param rows: список строк для вставки
    :param start_row: Номер первой строки
    :param url: адрес таблицы
    :param sheets_num: номер листа
    :param delta_col: смещение по столбцам
    :return:
    """
    if not rows:
        return
    logger.debug(f'Добавляем c {start_row}: {rows}')
    agcm = gspread_asyncio.AsyncioGspreadClientManager(get_creds)
    agc = await agcm.authorize()
    sheet = await agc.open_by_url(url)
    table = await sheet.get_worksheet(sheets_num)
    # await table.append_rows(rows)
    num_rows = len(rows)
    num_col = len(rows[0])
    logger.debug(f'{rowcol_to_a1(start_row, 1 + delta_col)}:{rowcol_to_a1(start_row + num_rows, num_col + delta_col)}')
    x = await table.batch_update([{
        'range': f'{rowcol_to_a1(start_row, 1 + delta_col)}:{rowcol_to_a1(start_row + num_rows, num_col + delta_col)}',
        'values': rows,
    }])
    print('x', x)
    return True




if __name__ == '__main__':
    pass






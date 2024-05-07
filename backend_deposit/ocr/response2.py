import asyncio
import itertools
import random
import time
from copy import copy
from io import BytesIO
from pathlib import Path

import aiohttp
import cv2
import numpy as np
import pytesseract

import django
import os

import requests
from PIL import Image
from PIL.ImageFile import ImageFile
from django.core.files import File

os.environ.setdefault("DJANGO_SETTINGS_MODULE", 'backend_deposit.settings')
django.setup()

from ocr.models import ScreenResponsePart, ScreenResponse

from backend_deposit.settings import BASE_DIR
from ocr.screen_response import screen_text_to_pay


async def create_response_part(bytes, black, white) -> str:
    """Создает новое распознавание скрина с заданными параметрами"""
    # screen, _ = ScreenResponse.objects.get_or_create(id=screen_id)
    # part_is_exist = ScreenResponsePart.objects.filter(screen=screen, black=black, white=white).exists()
    # if part_is_exist:
    #     return False
    # pytesseract.pytesseract.tesseract_cmd = 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'
    # img = cv2.imdecode(np.frombuffer(screen.image.file.read(), dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    # _, binary = cv2.threshold(img, black, white, cv2.THRESH_BINARY)
    # path = Path(screen.image.path)
    # new_file_path = path.parent
    # new_file_name = new_file_path / f'{path.stem}({black}-{white}).jpg'
    # cv2.imwrite(new_file_name.as_posix(), binary)
    # string = pytesseract.image_to_string(binary, lang='eng')
    ENDPOINT = 'http://127.0.0.1/ocr/response_screen_atb/'
    remote_screen_id = 1
    async with aiohttp.ClientSession() as session:
        async with session.post(ENDPOINT, data={'id': str(remote_screen_id), 'black': str(black), 'white': str(white), 'lang': 'rus', 'image': bytes}) as resp:
            status = resp.status
            text = await resp.json()
    return text


async def main():
    tespatch = Path('C:/') / 'Program Files' / 'Tesseract-OCR' / 'tesseract.exe'
    print(tespatch)
    # pytesseract.pytesseract.tesseract_cmd = 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'
    pytesseract.pytesseract.tesseract_cmd = tespatch.as_posix()

    path = BASE_DIR / 'test' / 'ocr_test' / 'bad_card2.jpg'

    black = 241
    white = 4
    # for black in range(30,255):
    #     for white in range(30, 255):
    # black = random.randint(0, 256)
    # white = random.randint(0, 256)
    print(black, white)
    # img = cv2.imdecode(np.frombuffer(screen.image.file.read(), dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    # pay = img[950:1060, :]
    pay = img[650:720, :]
    _, binary = cv2.threshold(pay, black, white, cv2.THRESH_BINARY)
    # cv2.imshow('imname', pay)
    # cv2.waitKey(0)
    string = pytesseract.image_to_string(img, timeout=10, lang='rus', config='--psm 6 --oem 0')
    # print(img.shape)

    bal = img[1230:1300, :]
    # cv2.imshow('imname', bal)
    # cv2.waitKey(0)
    _, binary =  cv2.threshold(bal, black, white, cv2.THRESH_BINARY)
    string2 = pytesseract.image_to_string(binary, timeout=10, lang='eng', config='--psm 7 --oem 2')
    print(string, string2)

    cv2.imwrite('coverted.jpg', binary)
    file = open(path, 'rb')
    text = await create_response_part(file.read(), black=black, white=white)
    print(text)


if __name__ == '__main__':
    asyncio.run(main())
    pass


import asyncio
import itertools
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
        async with session.post(ENDPOINT, data={'id': str(remote_screen_id), 'black': str(black), 'white': str(white), 'lang': 'eng', 'image': bytes}) as resp:
            status = resp.status
            text = await resp.json()
    return text



    # response = requests.post(ENDPOINT, data={'id': remote_screen_id, 'black': black, 'white': white}, files=screen, timeout=10)
    # print(response)
    # string = response.reason
    # text = string.replace('\n', ' ')
    # print(text)
    # print(text)
    # pay = screen_text_to_pay(text)
    # fields = ('response_date', 'recipient', 'sender', 'pay', 'transaction')
    # cut_pay = copy(pay)
    # for key, value in pay.items():
    #     if key not in fields:
    #         cut_pay.pop(key)
    # new_response_part, status = ScreenResponsePart.objects.get_or_create(screen=screen, black=black, white=white, **cut_pay)

    # return text


async def main():
    path = BASE_DIR / 'test' / 'ocr_test' / 'atb2.jpg'
    # screen, _ = ScreenResponse.objects.get_or_create(name=path.name)
    # if not screen.image:
    #     screen.image.save(content=file, name=f'{path.name}', save=False)
    #     screen.save()

    # blacks = screen.parts.values('black', 'white')
    # ready_pairs = set((x['black'], x['white']) for x in blacks)
    all_values = range(0, 256)
    comb = list(itertools.permutations(all_values, 2))
    print(len(comb))
    # for x in comb:
    #     print(x)
    # print(f'Распознанных частей для {screen}: {len(ready_pairs)} из {len(comb)}')
    unready_pairs = []
    for num, pair in enumerate(comb):
        # if pair in ready_pairs:
        #     continue
        unready_pairs.append(pair)

        # break
    chank = 10
    for i in range(0, len(unready_pairs), chank):
        pairs = unready_pairs[i:i+chank]
        tasks = []
        with open(path, "rb") as binary:
            binary = binary.read()
            for i in range(0, chank):
                task = create_response_part(binary, black=pairs[i][0], white=pairs[i][1])
                tasks.append(task)
            result = await asyncio.gather(*tasks)
            print(result)
            try:
                with open(f'{path.name}.txt', 'a', encoding='utf-8') as file:
                    for i in range(chank):
                        file.write(f'({pairs[i][0]}-{pairs[i][1]}) {str(result[i])}\n')
            except Exception as err:
                print(err)

    # create_response_part(screen.id, black=251, white=4)


if __name__ == '__main__':
    asyncio.run(main())
    pass


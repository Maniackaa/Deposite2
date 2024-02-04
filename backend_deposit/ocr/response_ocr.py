import itertools
import time
from copy import copy
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import pytesseract

import django
import os

from PIL import Image
from PIL.ImageFile import ImageFile
from django.core.files import File

os.environ.setdefault("DJANGO_SETTINGS_MODULE", 'backend_deposit.settings')
django.setup()

from ocr.models import ScreenResponsePart, ScreenResponse

from backend_deposit.settings import BASE_DIR
from ocr.screen_response import screen_text_to_pay


def create_response_part(screen_id, black, white) -> bool:
    """Создает новое распознавание скрина с заданными параметрами"""
    screen, _ = ScreenResponse.objects.get_or_create(id=screen_id)
    part_is_exist = ScreenResponsePart.objects.filter(screen=screen, black=black, white=white).exists()
    if part_is_exist:
        return False
    pytesseract.pytesseract.tesseract_cmd = 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'
    img = cv2.imdecode(np.frombuffer(screen.image.file.read(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    _, binary = cv2.threshold(img, black, white, cv2.THRESH_BINARY)
    path = Path(screen.image.path)
    new_file_path = path.parent
    new_file_name = new_file_path / f'{path.stem}({black}-{white}).jpg'
    cv2.imwrite(new_file_name.as_posix(), binary)
    string = pytesseract.image_to_string(binary, lang='rus')
    text = string.replace('\n', ' ')
    pay = screen_text_to_pay(text)
    fields = ('response_date', 'recipient', 'sender', 'pay', 'transaction')
    cut_pay = copy(pay)
    for key, value in pay.items():
        if key not in fields:
            cut_pay.pop(key)
    new_response_part, status = ScreenResponsePart.objects.get_or_create(screen=screen, black=black, white=white, **cut_pay)
    return status


def main():
    path = BASE_DIR / 'test' / 'ocr_test' / 'wrong57.jpg'
    file = open(path, 'rb')
    screen, _ = ScreenResponse.objects.get_or_create(name=path.name)
    if not screen.image:
        screen.image.save(content=file, name=f'{path.name}', save=False)
        screen.save()

    blacks = screen.parts.values('black', 'white')
    ready_pairs = set((x['black'], x['white']) for x in blacks)
    all_values = range(0, 256)
    comb = list(itertools.permutations(all_values, 2))
    print(len(comb))
    for x in comb:
        print(x)
    # print(f'Распознанных частей для {screen}: {len(ready_pairs)} из {len(comb)}')
    # unready_pairs = []
    # for pair in comb:
    #     if pair in ready_pairs:
    #         continue
    #     unready_pairs.append(pair)
    #     print(pair, create_response_part(screen.id, black=pair[0], white=pair[1]))
    #     break

if __name__ == '__main__':
    main()
    pass


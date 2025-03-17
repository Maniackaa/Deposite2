import asyncio
from io import BytesIO
from pathlib import Path
import cv2
import numpy as np
import pytesseract
import django
import os
from sys import platform

os.environ.setdefault("DJANGO_SETTINGS_MODULE", 'backend_deposit.settings')
django.setup()

def blur_sender_on_screen(img_source: Path | bytes):
    if platform == 'win32':
        tespatch = Path(
        'C:/') / 'Users' / 'mania' / 'AppData' / 'Local' / 'Programs' / 'Tesseract-OCR' / 'tesseract.exe'
        pytesseract.pytesseract.tesseract_cmd = tespatch.as_posix()
    if isinstance(img_source, Path):
        img_array = np.fromfile(img_source, dtype=np.uint8)
        orig_img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    else:
        orig_img = cv2.imdecode(np.frombuffer(img_source, dtype=np.uint8), cv2.IMREAD_COLOR)

    if orig_img is None:
        raise ValueError("Не удалось прочитать изображение")
    # Конвертируем изображение в оттенки серого
    gray = cv2.cvtColor(orig_img, cv2.COLOR_BGR2GRAY)

    # ✅ Улучшение контраста с помощью адаптивного выравнивания гистограммы
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # ✅ Применяем бинаризацию (регулируем границу, например, 150)
    threshold = 150
    _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)

    # ✅ Используем OCR для поиска текста
    custom_config = r'--psm 11 --oem 3'  # Улучшенная конфигурация
    data = pytesseract.image_to_data(binary, config=custom_config, output_type=pytesseract.Output.DICT)
    print(data)
    # ✅ Поиск строки "Sender" и замазывание номера телефона
    for i, text in enumerate(data['text']):
        if "Sender" in text:
            try:
                # Получаем координаты номера телефона (следующая строка после "Sender")
                x, y, w, h = data['left'][i + 1], data['top'][i + 1], data['width'][i + 1], data['height'][i + 1]

                # ✅ Размываем область перед замазыванием для плавного перехода
                roi = orig_img[y:y + h, x:x + w]
                blurred = cv2.GaussianBlur(roi, (15, 15), 10)  # Увеличьте ядро для сильного размытия
                orig_img[y:y + h, x:x + w] = blurred

                print(f"🔳 Замаскирован номер на позиции: {x}, {y}, {w}, {h}")

            except IndexError:
                print("⚠️ Ошибка: Не удалось определить номер после 'Sender'.")

    # Преобразуем итоговый бинаризированный образ в BytesIO
    success, encoded_image = cv2.imencode(".png", orig_img)
    if not success:
        raise ValueError("Ошибка при кодировании изображения")

    output_path = Path('blured.jpg')
    image_bytes_io = BytesIO(encoded_image.tobytes())
    # Сохранение из np.array
    # cv2.imwrite(output_path.as_posix(), orig_img)
    # print(f"✅ Изображение сохранено: {output_path}")
    # with open('res.jpg', 'wb') as f:
    #     f.write(image_bytes_io.read())

    return image_bytes_io


# source = Path("695597294_from_S_555417885.jpg")
# mask_sender_on_screen(source)

async def main():
    tesseract_path = Path(
        'C:/') / 'Users' / 'mania' / 'AppData' / 'Local' / 'Programs' / 'Tesseract-OCR' / 'tesseract.exe'
    pytesseract.pytesseract.tesseract_cmd = tesseract_path.as_posix()

    # ✅ Папка с исходными чеками
    input_folder = Path("checks")

    # ✅ Папка для сохранения обработанных чеков
    output_folder = input_folder / "masked"
    output_folder.mkdir(exist_ok=True)  # Создаем, если нет

    # ✅ Получаем список всех изображений в папке checks
    image_files = list(input_folder.glob("*.jpg"))  # Можно заменить на ("*.png") или ("*.*") для всех форматов

    print(f"📂 Найдено {len(image_files)} чеков для обработки.")

    # ✅ Обрабатываем каждый файл
    for image_path in image_files:
        # ✅ Путь к изображению
        print(image_path)

        # ✅ Загружаем изображение
        image = cv2.imread(str(image_path))

        # ✅ Конвертация в оттенки серого
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # ✅ Улучшение контраста с помощью адаптивного выравнивания гистограммы
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        # ✅ Применяем бинаризацию (регулируем границу, например, 150)
        threshold = 150
        _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)

        # ✅ Используем OCR для поиска текста
        custom_config = r'--psm 11 --oem 3'  # Улучшенная конфигурация
        data = pytesseract.image_to_data(binary, config=custom_config, output_type=pytesseract.Output.DICT)

        # ✅ Поиск строки "Sender" и замазывание номера телефона
        for i, text in enumerate(data['text']):
            if "Sender" in text:
                try:
                    # Получаем координаты номера телефона (следующая строка после "Sender")
                    x, y, w, h = data['left'][i + 1], data['top'][i + 1], data['width'][i + 1], data['height'][i + 1]

                    # ✅ Размываем область перед замазыванием для плавного перехода
                    roi = image[y:y + h, x:x + w]
                    blurred = cv2.GaussianBlur(roi, (15, 15), 10)  # Увеличьте ядро для сильного размытия
                    image[y:y + h, x:x + w] = blurred

                    print(f"🔳 Замаскирован номер на позиции: {x}, {y}, {w}, {h}")

                except IndexError:
                    print("⚠️ Ошибка: Не удалось определить номер после 'Sender'.")

        # ✅ Сохраняем измененное изображение
        output_path = output_folder / image_path.name
        cv2.imwrite(output_path.as_posix(), image)

        print(f"✅ Изображение сохранено: {output_path}")


if __name__ == '__main__':
    # asyncio.run(main())
    pass

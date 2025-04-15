import asyncio
import re
from pathlib import Path

import cv2
import pytesseract
import django
import os


os.environ.setdefault("DJANGO_SETTINGS_MODULE", 'backend_deposit.settings')
django.setup()


async def main():
    tesseract_path = Path('C:/Users/mania/AppData/Local/Programs/Tesseract-OCR/tesseract.exe')
    pytesseract.pytesseract.tesseract_cmd = tesseract_path.as_posix()

    # Папка с исходными чеками и папка для сохранения обработанных изображений
    input_folder = Path("checks")
    output_folder = input_folder / "masked"
    output_folder.mkdir(exist_ok=True)

    # Получаем список изображений
    image_files = list(input_folder.glob("*.jpg"))
    print(f"📂 Найдено {len(image_files)} изображений для обработки.")

    # Определяем список ключевых слов (обратите внимание, что для Transaction ID мы ожидаем объединённое значение)
    keys = ['Status', 'Date', 'Sender', 'wallet', 'Recipient', 'Transaction ID', 'Commission']

    results = {}

    for image_path in image_files:
        print(f"\nОбработка файла: {image_path.name}")
        image = cv2.imread(str(image_path))
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

        # Получаем данные OCR в виде словаря
        # char_whitelist = '+- :;*0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz,.•₼'
        # config = f'--psm 6 --oem 1 -c tessedit_char_whitelist="{char_whitelist}"'
        config = f'--psm 6 --oem 1'
        data = pytesseract.image_to_string(binary, lang='eng', config=config)
        print(data)
        data = pytesseract.image_to_data(binary, lang='eng', config=config, output_type=pytesseract.Output.DICT)
        print(data)

        # Собираем все непустые токены с координатами (top, left, текст)
        tokens = []
        n = len(data['text'])
        for i in range(n):
            token = data['text'][i].strip()
            if token:
                top = data['top'][i]
                left = data['left'][i]
                tokens.append((top, left, token))

        # Сортируем токены по (top, left)
        tokens.sort(key=lambda x: (x[0], x[1]))

        # Группируем токены по строкам – если разница по top не превышает tolerance
        tolerance = 10
        lines = []
        if tokens:
            current_line = [tokens[0]]
            current_top = tokens[0][0]
            for (top, left, token) in tokens[1:]:
                if top - current_top <= tolerance:
                    current_line.append((left, token))
                    current_top = top  # обновляем, чтобы строка была однородной
                else:
                    current_line.sort(key=lambda x: x[0])
                    lines.append(current_line)
                    current_line = [(left, token)]
                    current_top = top
            if current_line:
                current_line.sort(key=lambda x: x[0])
                lines.append(current_line)

        # Извлекаем данные из каждой строки
        extracted_data = {}
        for line in lines:
            i = 0
            while i < len(line):
                token = line[i][1]
                # Если токен равен "Transaction", устанавливаем ключ "Transaction ID"
                if token == "Transaction":
                    key_word = "Transaction ID"
                    i += 1
                    collected = []
                    while i < len(line) and line[i][1] not in keys:
                        collected.append(line[i][1])
                        i += 1
                    # Среди собранных токенов ищем числовой вариант с длиной >= 5 символов
                    value = ""
                    for part in collected:
                        part_clean = part.replace(',', '.')
                        try:
                            num = float(part_clean)
                            if len(part_clean) >= 5:
                                value = part_clean
                                break
                        except ValueError:
                            continue
                    extracted_data[key_word] = value
                    continue

                if token in keys:
                    key_word = token
                    i += 1
                    collected = []
                    while i < len(line) and line[i][1] not in keys:
                        collected.append(line[i][1])
                        i += 1
                    value = " ".join(collected).strip()
                    # Для Commission оставляем только первое числовое значение
                    if key_word == "Commission":
                        parts = value.split()
                        num = ""
                        for part in parts:
                            try:
                                float(part.replace(',', '.'))
                                num = part
                                break
                            except ValueError:
                                continue
                        value = num
                    extracted_data[key_word] = value
                else:
                    i += 1

        # === ОБРАБОТКА Recipient ===
        recipient_value = ""
        for i in range(len(data['text'])):
            token = data['text'][i].strip()
            if token == "Recipient":
                x = data['left'][i]
                y = data['top'][i]
                w = data['width'][i]
                h = data['height'][i]

                # Начинаем справа от "Recipient", по высоте чуть шире, по ширине до конца строки
                roi_x1 = x + w + 5
                roi_y1 = y - 10
                roi_y2 = y + h + 10
                roi_x2 = binary.shape[1]  # до самого конца изображения по ширине

                # Защита от выхода за границы
                roi_x1 = max(0, roi_x1)
                roi_y1 = max(0, roi_y1)
                roi_y2 = min(binary.shape[0], roi_y2)

                roi = binary[roi_y1:roi_y2, roi_x1:roi_x2]

                # Предобработка ROI: масштаб, контраст, инверсия
                roi_resized = cv2.resize(roi, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                roi_contrast = clahe.apply(roi_resized)
                _, roi_thresh = cv2.threshold(roi_contrast, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                roi_inverted = cv2.bitwise_not(roi_thresh)

                # Распознаём строку целиком без ограничений
                config = '--psm 7 --oem 1'
                recipient_value = pytesseract.image_to_string(roi_inverted, lang='eng', config=config).strip()

                def clean_recipient_text(text):
                    text = text.strip()
                    if text.startswith('+994'):
                        return re.sub(r'\s+', ' ', text)
                    cleaned = re.sub(r'[+=\-]', '', text)
                    parts = cleaned.split()

                    # Попытка собрать по шаблону: первые 2 блока и последние 4 цифры
                    if len(parts) >= 3:
                        return f"{parts[0]} {parts[1]}•• •••• {parts[-1]}"
                    elif len(parts) == 2:
                        return f"{parts[0]} •• •••• {parts[1]}"
                    elif len(parts) == 1 and len(parts[0]) >= 4:
                        return f"{parts[0][:4]} •• •••• {parts[0][-4:]}"
                    else:
                        return '•••• •••• •••• ••••'  # если всё совсем плохо — вернуть маску

                cleaned = clean_recipient_text(recipient_value)
                # Убираем лишние пробелы
                print(f"📦 Recipient (по координатам): {cleaned}")

                extracted_data['Recipient'] = cleaned


        # === ДОБАВЛЯЕМ РАСПОЗНАВАНИЕ СУММЫ (AMOUNT) ===
        amount = ""
        potential_amounts = []

        for i in range(len(data['text'])):

            token = data['text'][i].strip().replace('₼', '').replace('m', '')
            if re.fullmatch(r'-?\d+\.\d{2}', token):  # например -1.00, 11.00
                x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
                center_x = x + w // 2
                center_y = y + h // 2
                potential_amounts.append({
                    'text': token,
                    'center_x': center_x,
                    'center_y': center_y,
                    'area': w * h
                })

        if potential_amounts:
            # 💡 Сортируем по наибольшей площади или близости к центру экрана
            img_h, img_w = binary.shape[:2]
            center_screen = (img_w // 2, img_h // 2)

            def score(p):
                # Расстояние до центра экрана + инвертированная площадь (приоритет — по центру и крупнее)
                dx = abs(p['center_x'] - center_screen[0])
                dy = abs(p['center_y'] - center_screen[1])
                return dx + dy - p['area'] * 0.01

            best_match = sorted(potential_amounts, key=score)[0]
            amount = best_match['text']
            print(f"💰 Найдена сумма (amount): {amount}")
        else:
            print("❌ Сумма не найдена по шаблону.")

        extracted_data['Amount'] = amount
        wallet = extracted_data.get('wallet')
        if wallet:
            extracted_data['wallet'] = wallet.replace(' ', '')

        # Выводим результаты для каждого ключа
        print("Извлеченные данные:")
        for k in keys:
            print(f"Ключ: '{k}', строка после: '{extracted_data.get(k, '')}'")
        results[image_path.name] = extracted_data
        print(data)
        # Сохраняем изображение
        output_path = output_folder / image_path.name
        # cv2.imwrite(output_path.as_posix(), image)
        cv2.imwrite(output_path.as_posix(), binary)
        print(f"✅ Изображение сохранено: {output_path}")

    print("\nСобранные результаты:")
    for filename, res in results.items():
        print(f"{filename}: {res}")


if __name__ == '__main__':
    asyncio.run(main())
from pathlib import Path

import pytesseract
from PIL import Image
import re


# Укажи путь к tesseract, если он не в PATH
tesseract_path = Path('C:/Users/mania/AppData/Local/Programs/Tesseract-OCR/tesseract.exe')
pytesseract.pytesseract.tesseract_cmd = tesseract_path

def extract_transfer_details(image_path):
    img = Image.open(image_path)
    raw_text = pytesseract.image_to_string(img, lang='eng', config='--psm 6')
    print("\n[DEBUG] Распознанный текст:\n", raw_text)
    result = {}

    # Читаем строку за строкой
    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]

    # Примерная логика поиска
    for i, line in enumerate(lines):
        # Банк
        if "Kapital Bank" in line:
            result['Банк'] = "Kapital Bank"

        # Сумма
        if re.match(r"^-?\d+\.\d{2}", line):
            result['Сумма'] = line.strip()

        # Тип операции
        if "Transfer to card" in line:
            result['Тип операции'] = "Transfer to card"

        # Статус
        if "Status" in line and i + 1 < len(lines):
            result['Статус'] = lines[i + 1]

        # Дата
        if "Date" in line and i + 1 < len(lines):
            result['Дата'] = lines[i + 1]

        # Отправитель
        if "Sender" in line and i + 1 < len(lines):
            result['Отправитель'] = lines[i + 1]

        # Получатель
        if "Recipient" in line and i + 1 < len(lines):
            result['Получатель (карта)'] = lines[i + 1]

        # Транзакция
        if "Transaction ID" in line and i + 1 < len(lines):
            result['ID транзакции'] = lines[i + 1]

        # Комиссия
        if "Commission" in line and i + 1 < len(lines):
            result['Комиссия'] = lines[i + 1]

    return result


input_folder = Path("checks")
image_files = list(input_folder.glob("*.jpg"))
print(image_files)
print(f"📂 Найдено {len(image_files)} изображений для обработки.")
# Пример использования

for image_path in image_files[-1:]:
    result = extract_transfer_details(image_path.as_posix())
    print(result)

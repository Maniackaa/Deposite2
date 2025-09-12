import re
import requests
import ast

LOG_FILE = "log.2"   # путь к файлу с логами
URL = "http://193.124.33.223/sms/"  # куда слать
# URL = "http://127.0.0.1:8000/sms/"

with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
    text = f.read()

# Найти все <QueryDict: {...}>
blocks = re.findall(r"<QueryDict:\s*({.*?})>", text, flags=re.DOTALL)

# Убрать дубли, сохранив порядок
unique_blocks = list(dict.fromkeys(blocks))

print(f"Всего найдено: {len(blocks)}, уникальных: {len(unique_blocks)}")

for i, block in enumerate(unique_blocks, 1):
    try:
        # Превратить строку "{'id': ['...'], 'message': ['...']}" в dict
        raw = ast.literal_eval(block)
        if not isinstance(raw, dict):
            print(f"[{i}] skip: не dict")
            continue

        # Превращаем списки в значения (берём первый элемент)
        data = {k: (v[0] if isinstance(v, list) and v else v) for k, v in raw.items()}

        # Шлём как обычную форму
        resp = requests.post(URL, data=data)
        print(f"[{i}] code={resp.status_code} len={len(resp.text)}")
    except Exception as e:
        print(f"[{i}] ERROR {e}")
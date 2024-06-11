import asyncio
import json

import aiohttp

from backend_deposit.settings import BASE_DIR


async def response_from_bytes(bytes, black, white, oem='0', psm='7') -> str:
    ENDPOINT = 'http://localhost/ocr/response_bank1/'
    ENDPOINT = 'http://localhost/ocr/response_text/'
    # ENDPOINT = 'http://127.0.0.1:8000/ocr/response_bank1/'

    async with aiohttp.ClientSession() as session:
        async with session.post(ENDPOINT,
                                data={'black': str(black), 'white': str(white), 'lang': 'eng', 'oem': oem, 'psm': psm, 'image': bytes}) as resp:
            status = resp.status
            # print(status)
            result = await resp.json()
            # print(result)
            # text = json.loads(result)
            # print(text)
    return result


async def main():
    path = BASE_DIR / 'test' / 'ocr_test' / '86000.jpg'
    with open(path, "rb") as binary:
        binary = binary.read()
        for i in range(0, 256):
            text = await response_from_bytes(binary, i, 255)
            print(i, text)
        # text = await response_from_bytes(binary, 175, 255, oem='1', psm='7')


if __name__ == '__main__':
    asyncio.run(main())

    pass


import asyncio

import aiohttp

from backend_deposit.settings import BASE_DIR


async def response_from_bytes(bytes, black, white, oem='0', psm='6') -> str:
    ENDPOINT = 'http://localhost/ocr/response_screen_m10/'
    async with aiohttp.ClientSession() as session:
        async with session.post(ENDPOINT,
                                data={'black': str(black), 'white': str(white), 'lang': 'rus', 'oem': oem, 'psm': psm, 'image': bytes}) as resp:
            status = resp.status
            # print(status)
            # text = resp.reason
            # print(text)
            data = await resp.json()
            text = data.get('text')
    return text


async def main():
    path = BASE_DIR / 'test' / 'ocr_test' / 'm10c.jpg'
    with open(path, "rb") as binary:
        binary = binary.read()
        # for i in range(0, 256):
        #     text = await response_from_bytes(binary, i, 255)
        #     print(i, text.replace('\n', ' '))
        text = await response_from_bytes(binary, 175, 255, oem='1', psm='6')
        print(text)

if __name__ == '__main__':
    asyncio.run(main())

    pass


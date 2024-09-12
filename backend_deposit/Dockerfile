FROM python:3.10
WORKDIR /app
RUN apt-get update && apt-get install libpq-dev libgl1 tesseract-ocr -y
RUN pip install --upgrade pip
RUN pip install gunicorn==20.1.0
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
COPY rus.traineddata /usr/share/tesseract-ocr/5/tessdata/
COPY rus.traineddata /usr/share/tesseract-ocr/4.00/tessdata/
COPY rus.traineddata /usr/local/share/tessdata/
COPY rus.traineddata /usr/local/share/tessdata/
COPY eng.traineddata /usr/share/tesseract-ocr/5/tessdata/
COPY eng.traineddata /usr/share/tesseract-ocr/4.00/tessdata/
COPY eng.traineddata /usr/local/share/tessdata/
COPY eng.traineddata /usr/local/share/tessdata/
RUN ["chmod", "u+x", "/app/entrypoint.sh"]
#ENTRYPOINT ["/app/entrypoint.sh"]

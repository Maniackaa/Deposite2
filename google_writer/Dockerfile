FROM python:3.10
WORKDIR /app
RUN apt-get update
COPY requirements.txt .
#RUN pip install -r requirements.txt --no-cache-dir
RUN pip install -r requirements.txt
COPY . .

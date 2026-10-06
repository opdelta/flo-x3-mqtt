FROM python:3.11-alpine

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py discover_stations.py debug.py /app/
COPY flo_client /app/flo_client
RUN mkdir -p /app/data

ENTRYPOINT ["python", "-u", "main.py"]

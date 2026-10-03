FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY orion_exporter.py .

USER 10001

EXPOSE 7001

CMD ["python", "orion_exporter.py"]
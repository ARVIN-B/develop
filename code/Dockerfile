FROM python:3.11-slim

LABEL maintainer="Hamerz Plasma IT Engineering Team"
LABEL description="سامانه جامع عملیات کارگاهی و کنترل پروژه شرکت هامرز پلاسما"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

RUN mkdir -p /app/uploads/cad_drop /app/uploads/voice_logs /app/uploads/qc_reports /app/uploads/drawings

EXPOSE 8091

ENV PORT=8091
ENV PYTHONUNBUFFERED=1

CMD ["python3", "app.py"]

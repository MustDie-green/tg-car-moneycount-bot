FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Отдельный слой с зависимостями: пересобирается только при правке requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Шрифты лежат в репозитории. Если их нет — картинки не нарисуются,
# и узнать об этом лучше при сборке, а не в рантайме.
RUN test -f assets/fonts/Inter-Regular.ttf || \
    (echo 'Нет шрифтов в assets/fonts. Запусти: python scripts/prepare_fonts.py' && exit 1)

CMD ["python", "-m", "carbot"]

# Bolo reminder copilot - demo container.
#
# Build:  docker build -t bolo .
# Run:    docker run --rm -p 8000:8000 --env-file .env -v bolo_media:/app/media_root bolo
# Or:     docker compose up
#
# The container runs migrations, seeds the fictional demo data (first boot
# only; `manage.py seed_demo --force` resets it) and serves the app on :8000
# (ASGI, so the live WebSocket feed works too). SQLite lives inside the
# container; the named volume keeps generated voice audio across restarts.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=notification_project.settings

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate && python manage.py seed_demo && python manage.py runserver 0.0.0.0:8000 --noreload"]

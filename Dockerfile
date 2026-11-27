# The notes desk app. compose.yaml runs it next to a PostgreSQL container.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NOTES_DESK_DOCKER=1
# tzdata: time-zone data for New York market hours (the slim image may not include it)
RUN pip install --no-cache-dir "psycopg[binary]==3.2.3" "tzdata==2025.2"
WORKDIR /app
COPY . .
EXPOSE 8000
CMD ["python3", "docker/start.py"]

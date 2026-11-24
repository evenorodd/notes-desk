# The notes desk app. compose.yaml runs it next to a PostgreSQL container.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "psycopg[binary]==3.2.3"
WORKDIR /app
COPY . .
EXPOSE 8000
CMD ["python3", "docker/start.py"]

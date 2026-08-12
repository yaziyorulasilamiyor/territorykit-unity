FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

COPY tests ./tests

EXPOSE 8000

CMD ["uvicorn", "geometry_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

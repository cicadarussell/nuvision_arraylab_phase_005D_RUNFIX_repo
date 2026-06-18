FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .
COPY app /app/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

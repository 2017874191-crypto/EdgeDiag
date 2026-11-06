FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY protocol ./protocol
COPY services ./services
COPY device_simulator ./device_simulator
COPY agent ./agent
RUN pip install --no-cache-dir .
CMD ["uvicorn", "services.app:app", "--host", "0.0.0.0", "--port", "8000"]

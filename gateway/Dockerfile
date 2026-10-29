FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 gateway && mkdir -p /data && chown gateway /data
USER gateway

ENV GATEWAY_DATA_DIR=/data
EXPOSE 8080
CMD ["python", "-m", "gateway.main"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POKESELL_DB_PATH=/app/data/pokesell_ledger.db \
    POKESELL_BIND_PORT=8080

WORKDIR /app

# Create non-root runtime user for Principle of Least Privilege
RUN groupadd --gid 10001 pokesell && \
    useradd --uid 10001 --gid pokesell --create-home pokesell && \
    mkdir -p /app/data && chown -R pokesell:pokesell /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pokesell_agent ./pokesell_agent
COPY eval ./eval
COPY pyproject.toml README.md ./
RUN chown -R pokesell:pokesell /app

USER pokesell

EXPOSE 8080

CMD ["uvicorn", "pokesell_agent.web_server:web_app", "--host", "0.0.0.0", "--port", "8080"]

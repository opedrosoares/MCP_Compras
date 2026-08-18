FROM python:3.11-slim

WORKDIR /app

# Copia o necessario para instalar o pacote
COPY pyproject.toml README.md ./
COPY src ./src

# Instalacao normal (nao editable) — gera o entry point `compras-mcp` no PATH
RUN pip install --no-cache-dir .

# Railway injeta PORT como env var; o server detecta e sobe em HTTP
ENV PORT=8000
EXPOSE 8000

CMD ["compras-mcp"]

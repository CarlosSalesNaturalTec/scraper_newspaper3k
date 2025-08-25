# Use uma imagem base oficial do Python.
FROM python:3.11-slim

# Defina o diretório de trabalho no contêiner.
WORKDIR /app

# Copie o arquivo de dependências para o diretório de trabalho.
COPY requirements.txt .

# Instale as dependências.
RUN pip install --no-cache-dir -r requirements.txt

# Copie o resto do código da aplicação para o diretório de trabalho.
COPY . .

# Exponha a porta que o Uvicorn usará.
EXPOSE 8000

# Comando para executar a aplicação.
# O host 0.0.0.0 torna o servidor acessível externamente (necessário para o Cloud Run).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

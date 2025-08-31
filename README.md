# Documentação do Módulo Scraper (Newspaper3k)

Este documento detalha a arquitetura e o funcionamento do micro-serviço de scraping, uma peça fundamental na pipeline de processamento de dados da plataforma.

## 1. Detalhes Técnicos

Este serviço é uma API FastAPI focada em uma única tarefa: extrair conteúdo textual de artigos da web.

- **Framework Principal:** [FastAPI](https://fastapi.tiangolo.com/) para criar o endpoint que dispara o processo.
- **Biblioteca de Scraping:** [Newspaper3k](https://newspaper.readthedocs.io/en/latest/) é a biblioteca central usada para baixar e extrair o conteúdo principal de URLs.
- **Banco de Dados:** Utiliza o SDK `firebase-admin` para ler e atualizar documentos no **Google Firestore**.
- **Execução Assíncrona:** O processo de scraping é executado como uma tarefa em background (`BackgroundTasks`) para que a chamada à API retorne imediatamente, permitindo que o processo de coleta (que pode ser demorado) continue de forma independente.

## 2. Instruções de Uso e Implantação

### 2.1. Configuração do Ambiente Local

1.  **Credenciais de Serviço do Firebase:**
    -   Coloque o arquivo JSON da sua Service Account do Firebase dentro da pasta `config/`.
    -   O `.gitignore` está configurado para ignorar esta pasta.

2.  **Variáveis de Ambiente:**
    -   Copie `.env.example` para `.env`.
    -   Defina `GOOGLE_APPLICATION_CREDENTIALS` com o caminho para o seu arquivo de credenciais.

    ```bash
    # .env
    GOOGLE_APPLICATION_CREDENTIALS=./config/your-service-account-file.json
    ```

3.  **Instalação e Execução:**
    ```bash
    # Navegue até a pasta do scraper
    cd scraper_newspaper3k

    # Crie e ative um ambiente virtual
    python -m venv venv
    .\venv\Scripts\activate

    # Instale as dependências
    pip install -r requirements.txt

    # Execute o servidor
    uvicorn main:app --reload
    ```
    A API estará disponível em `http://127.0.0.1:8000`.

### 2.2. Implantação (Google Cloud Run)

O serviço é projetado para ser implantado como um contêiner no Google Cloud Run.

```bash
# Substitua [PROJECT_ID] pelo ID do seu projeto no GCP
gcloud builds submit --tag gcr.io/[PROJECT_ID]/scraper-newspaper3k ./scraper_newspaper3k

gcloud run deploy scraper-newspaper3k \
  --image gcr.io/[PROJECT_ID]/scraper-newspaper3k \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8000
```

**Nota de Produção:** Em um ambiente de produção, o ideal é que este serviço não seja `allow-unauthenticated`. A invocação deve ser feita de forma segura por um serviço autorizado, como o Google Cloud Scheduler, utilizando uma conta de serviço com as permissões necessárias.

## 3. Relação com Outros Módulos

Este serviço opera como um "worker" assíncrono na pipeline de dados da plataforma. Ele não é chamado diretamente por outros serviços, mas reage aos dados presentes no Firestore.

### 3.1. Orquestração (Google Cloud Scheduler)

-   Em produção, o endpoint `/scrape` deste serviço é projetado para ser acionado periodicamente (ex: a cada 15 minutos) por um **Google Cloud Scheduler**.
-   Cada chamada ao scheduler inicia uma nova "leva" de processamento de URLs pendentes.

### 3.2. Interação com o Firestore

O Firestore é o ponto central de comunicação. O scraper interage principalmente com duas coleções:

-   **`monitor_results` (Leitura e Escrita):**
    -   **Fonte de Dados:** O serviço busca documentos nesta coleção onde o campo `status` é igual a `pending` ou `reprocess`.
    -   **Lógica de Processamento:** Para cada documento encontrado, o scraper executa os seguintes passos:
        1.  **Filtra Domínios:** Se o domínio for de uma rede social (YouTube, Instagram, etc.), atualiza o status para `scraper_skipped`.
        2.  **Calcula Relevância:** Avalia a URL com base em critérios como termos no título, snippet e confiabilidade do domínio. Se a pontuação for < 0.5, atualiza o status para `relevance_failed`.
        3.  **Executa o Scraping:** Se passar nos filtros, usa o `newspaper3k` para extrair o conteúdo.
        4.  **Atualiza o Status Final:**
            -   Em caso de sucesso, atualiza o documento com o conteúdo extraído (`scraped_content`, `scraped_title`, etc.) e define o `status` como `scraper_ok`.
            -   Em caso de falha, atualiza o `status` para `scraper_failed` e registra a mensagem de erro.

-   **`system_logs` (Apenas Escrita):**
    -   Quando o endpoint `/scrape` é acionado, ele imediatamente cria um novo documento nesta coleção com o status `started`.
    -   Ao final da tarefa em background, o mesmo documento é atualizado com o status `completed` ou `failed`, o número de URLs processadas e o horário de término. Isso permite monitorar a saúde e o histórico de execuções do scraper.

### 3.3. Módulo NLP (Próximo na Pipeline)

-   O trabalho do scraper termina ao definir o status de um documento como `scraper_ok`.
-   Este status serve como um gatilho para o próximo serviço na pipeline, o **Módulo de NLP**, que por sua vez buscará por documentos com este status para realizar a análise de sentimento e extração de entidades.
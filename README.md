# scraper_newspaper3k

Este é um micro-serviço FastAPI projetado para extrair conteúdo de artigos da web usando a biblioteca `newspaper3k`. Ele é parte da Plataforma de Social Listening.

## Funcionalidades

- **Recebe URLs do Firestore**: Busca por URLs que precisam ser processadas.
- **Filtra Domínios**: Ignora URLs de redes sociais como YouTube, Instagram e Facebook.
- **Calcula Relevância**: Antes de processar, atribui uma pontuação de relevância à URL com base em critérios como a presença de termos-chave, a confiabilidade do domínio e a estrutura da URL.
- **Extrai Conteúdo**: Para URLs relevantes, utiliza o `newspaper3k` para extrair o texto principal, título, autores e data de publicação.
- **Atualiza Status**: Atualiza o documento no Firestore com o conteúdo extraído e o novo status (`scraper_ok`, `relevance_failed`, `scraper_failed`, etc.).

## Configuração do Ambiente Local

1.  **Crie um Ambiente Virtual**:
    ```bash
    python -m venv venv
    ```

2.  **Ative o Ambiente Virtual**:
    -   Windows:
        ```bash
        .\venv\Scripts\activate
        ```
    -   macOS/Linux:
        ```bash
        source venv/bin/activate
        ```

3.  **Instale as Dependências**:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Credenciais do Firebase**:
    -   Crie uma pasta `config` na raiz deste projeto (`scraper_newspaper3k/config`).
    -   Coloque seu arquivo de credenciais da Service Account do Firebase dentro da pasta `config`.
    -   **IMPORTANTE**: Renomeie o arquivo de credenciais para `firebase_credentials.json` ou defina a variável de ambiente `GOOGLE_APPLICATION_CREDENTIALS` com o caminho para o seu arquivo.

## Executando Localmente

Para iniciar o servidor de desenvolvimento, execute o seguinte comando no terminal:

```bash
uvicorn main:app --reload
```

A API estará disponível em `http://127.0.0.1:8000`.

## Endpoint

### `POST /scrape`

-   **Descrição**: Dispara o processo de busca e scraping de URLs pendentes no Firestore.
-   **Corpo da Requisição**: Vazio.
-   **Resposta de Sucesso (202 Accepted)**:
    ```json
    {
      "message": "Processo de scraping iniciado.",
      "urls_processed_now": 5
    }
    ```
-   **Resposta de Erro (503 Service Unavailable)**:
    ```json
    {
      "detail": "Conexão com o Firestore não está disponível."
    }
    ```

## Implantação (Google Cloud Run)

Este serviço é projetado para ser implantado como um contêiner no Google Cloud Run.

1.  **Construa a Imagem Docker**:
    ```bash
    gcloud builds submit --tag gcr.io/[PROJECT_ID]/scraper-newspaper3k .
    ```
    Substitua `[PROJECT_ID]` pelo ID do seu projeto no Google Cloud.

2.  **Faça o Deploy no Cloud Run**:
    ```bash
    gcloud run deploy scraper-newspaper3k \
      --image gcr.io/[PROJECT_ID]/scraper-newspaper3k \
      --platform managed \
      --region us-central1 \
      --allow-unauthenticated \
      --port 8000
    ```
    **Nota**: O comando acima permite acesso não autenticado. Para produção, você deve configurar a autenticação do IAM e invocar o serviço de forma segura, por exemplo, a partir do Cloud Scheduler com uma conta de serviço autorizada.

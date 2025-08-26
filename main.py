# -*- coding: utf-8 -*-
import os
import firebase_admin
from firebase_admin import credentials, firestore
from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from newspaper import Article
from newspaper.article import ArticleException
from urllib.parse import urlparse
import datetime
from dotenv import load_dotenv

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

# --- Configuração do Firebase ---
try:
    # O caminho para o arquivo de credenciais deve ser configurado via variável de ambiente
    cred_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', './config/firebase_credentials.json')
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    print(f"Erro ao inicializar o Firebase Admin: {e}")
    # Em um ambiente de produção, você pode querer que a aplicação pare se o DB não conectar.
    # Para desenvolvimento, podemos permitir que continue para testes de endpoints sem DB.
    db = None

app = FastAPI(
    title="Serviço de Scraping com Newspaper3k",
    description="Um micro-serviço para extrair conteúdo de URLs, calcular relevância e salvar no Firestore.",
    version="1.0.0"
)

# --- Lógica de Relevância ---
TRUSTED_DOMAINS = {
    'g1.globo.com', 'www.uol.com.br', 'www.folha.uol.com.br', 'www.estadao.com.br',
    'veja.abril.com.br', 'www.cnnbrasil.com.br', 'www.cartacapital.com.br',
    'www.poder360.com.br', 'www.metropoles.com', 'www.oantagonista.com.br',
    'www.bbc.com', 'www.nytimes.com', 'www.theguardian.com', 'www.reuters.com',
    'www.wsj.com', 'www.bloomberg.com', 'apnews.com'
}

SOCIAL_MEDIA_DOMAINS = {'www.youtube.com', 'youtube.com', 'www.instagram.com', 'instagram.com', 'www.facebook.com', 'facebook.com'}

def calculate_relevance(url_data: dict) -> float:
    """
    Calcula a pontuação de relevância de uma URL com base em critérios predefinidos.
    """
    score = 0
    total_weight = 100  # Soma de todos os pesos

    term = url_data.get('term', '').lower()
    title = url_data.get('title', '').lower()
    snippet = url_data.get('snippet', '').lower()
    link = url_data.get('link', '')
    domain = urlparse(link).netloc

    # 1. Presença do termo exato no title (Peso: 30)
    if term in title:
        score += 30

    # 2. Presença do termo no snippet (Peso: 10)
    if term in snippet:
        score += 10

    # 3. Domínio confiável (Peso: 25)
    if domain in TRUSTED_DOMAINS:
        score += 25

    # 4. URL amigável (Peso: 5)
    if '?' not in link and '&' not in link:
        score += 5

    # 5. Título com múltiplos termos úteis (Peso: 10)
    #    Simplificação: se o título tiver mais de 3 palavras, consideramos útil.
    if len(title.split()) > 3:
        score += 10

    # 6. Data da publicação (quando disponível) (Peso: 20)
    #    Este critério será aplicado após o scraping, se a data for encontrada.
    #    Por enquanto, o cálculo inicial é baseado em um máximo de 80.
    #    Vamos normalizar para 100 para manter a consistência.
    
    # Normaliza o score para uma escala de 0 a 1
    # O peso total considerado aqui é 80 (30+10+25+5+10)
    current_max_score = 80
    if current_max_score == 0: return 0.0
    
    normalized_score = score / current_max_score
    
    return round(normalized_score, 2)

# --- Funções de Background ---
def scrape_and_update(run_id: str):
    """
    Busca URLs, faz o scraping e atualiza o Firestore.
    Esta função é projetada para ser executada em background.
    O status final da execução é registrado no log correspondente ao run_id.
    """
    if not db:
        print(f"Firestore não está disponível para a tarefa {run_id}. A tarefa de scraping em background não pode ser executada.")
        return

    log_ref = db.collection('system_logs').document(run_id)
    processed_count = 0
    try:
        urls_ref = db.collection('monitor_results')
        docs_to_process = urls_ref.where('status', 'in', ['pending', 'reprocess']).stream()

        for doc in docs_to_process:
            url_data = doc.to_dict()
            doc_id = doc.id
            link = url_data.get('link')

            if not link:
                urls_ref.document(doc_id).update({'status': 'scraper_failed', 'error_message': 'URL não encontrada no documento.'})
                continue

            domain = urlparse(link).netloc
            if domain in SOCIAL_MEDIA_DOMAINS:
                urls_ref.document(doc_id).update({'status': 'scraper_skipped', 'reason': 'Social media domain'})
                continue

            relevance_score = calculate_relevance(url_data)
            
            if relevance_score < 0.50:
                urls_ref.document(doc_id).update({'status': 'relevance_failed', 'relevance_score': relevance_score})
                continue

            try:
                article = Article(link, language='pt')
                article.download()
                article.parse()

                if article.publish_date:
                    if article.publish_date > (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=730)):
                        relevance_score = ((relevance_score * 80) + 20) / 100

                update_data = {
                    'status': 'scraper_ok',
                    'scraped_content': article.text,
                    'scraped_title': article.title,
                    'authors': article.authors,
                    'publish_date': article.publish_date,
                    'relevance_score': relevance_score,
                    'last_processed_at': datetime.datetime.now(datetime.timezone.utc)
                }
                urls_ref.document(doc_id).update(update_data)
                processed_count += 1

            except ArticleException as e:
                urls_ref.document(doc_id).update({'status': 'scraper_failed', 'error_message': f"Newspaper3k error: {str(e)}",'last_processed_at': datetime.datetime.now(datetime.timezone.utc)})
            except Exception as e:
                urls_ref.document(doc_id).update({'status': 'scraper_failed', 'error_message': f"Erro inesperado: {str(e)}",'last_processed_at': datetime.datetime.now(datetime.timezone.utc)})
        
        log_ref.update({'status': 'completed', 'end_time': datetime.datetime.now(datetime.timezone.utc), 'processed_count': processed_count})
        print(f"Processo de scraping em background (run_id: {run_id}) concluído. {processed_count} URLs processadas.")

    except Exception as e:
        error_msg = f"Erro geral na tarefa de scraping: {str(e)}"
        print(error_msg)
        try:
            log_ref.update({'status': 'failed', 'end_time': datetime.datetime.now(datetime.timezone.utc), 'error_message': error_msg, 'processed_count': processed_count})
        except Exception as log_e:
            print(f"Falha ao registrar o erro no log de scraping (run_id: {run_id}): {log_e}")


# --- Endpoints ---
@app.get("/")
def read_root():
    return {"message": "Scraper Newspaper3k está no ar!"}

@app.post("/scrape", status_code=status.HTTP_202_ACCEPTED)
async def trigger_scraping(background_tasks: BackgroundTasks):
    """
    Inicia o processo de scraping em background e registra um log da execução.

    Este endpoint busca por URLs no Firestore com status 'pending' ou 'reprocess',
    avalia sua relevância, realiza o scraping e atualiza o status no banco.
    A resposta é imediata e o processo continua em background.
    Um log da execução é criado na coleção `system_logs`.
    """
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conexão com o Firestore não está disponível."
        )
    
    # --- Log da Execução ---
    log_data = {
        'task': 'scraper',
        'start_time': datetime.datetime.now(datetime.timezone.utc),
        'status': 'started'
    }
    try:
        # Adiciona o log e obtém a referência do documento
        update_time, log_ref = db.collection('system_logs').add(log_data)
        run_id = log_ref.id
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Falha ao criar o log da tarefa no Firestore: {e}")
    
    background_tasks.add_task(scrape_and_update, run_id)

    return {"message": "Processo de scraping em background iniciado.", "run_id": run_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

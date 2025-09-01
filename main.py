# -*- coding: utf-8 -*-
import os
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.api_core import retry
from google.api_core.exceptions import GoogleAPICallError, RetryError
from fastapi import FastAPI, HTTPException, status, BackgroundTasks
from newspaper import Article
from newspaper.article import ArticleException
from urllib.parse import urlparse
import datetime
import time
import logging
from dotenv import load_dotenv
from models.schemas import SystemLog

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

# --- Configuração do Firebase ---
def initialize_firebase():
    """Inicializa o Firebase com tratamento de erro robusto."""
    try:
        # O caminho para o arquivo de credenciais deve ser configurado via variável de ambiente
        cred_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', './config/firebase_credentials.json')
        cred = credentials.Certificate(cred_path)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        
        # Testa a conexão
        db = firestore.client()
        # Teste simples de conectividade
        db.collection('_health_check').limit(1).get()
        logger.info("Firebase inicializado com sucesso")
        return db
    except Exception as e:
        logger.error(f"Erro ao inicializar o Firebase Admin: {e}")
        return None

db = initialize_firebase()

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

SOCIAL_MEDIA_DOMAINS = {
    'www.youtube.com', 'youtube.com', 'www.instagram.com', 'instagram.com', 
    'www.facebook.com', 'facebook.com', 'twitter.com', 'www.twitter.com',
    'x.com', 'www.x.com'
}

def calculate_relevance(url_data: dict) -> float:
    """
    Calcula a pontuação de relevância de uma URL com base em critérios predefinidos.
    """
    score = 0
    term = url_data.get('term', '').lower()
    title = url_data.get('title', '').lower()
    snippet = url_data.get('snippet', '').lower()
    link = url_data.get('link', '')
    domain = urlparse(link).netloc

    if term in title: score += 30
    if term in snippet: score += 10
    if domain in TRUSTED_DOMAINS: score += 25
    if '?' not in link and '&' not in link: score += 5
    if len(title.split()) > 3: score += 10
    
    current_max_score = 80
    if current_max_score == 0: return 0.0
    
    normalized_score = score / current_max_score
    return round(normalized_score, 2)

def safe_firestore_operation(operation, max_retries=3, delay=1):
    """
    Executa operações do Firestore com retry automático.
    """
    for attempt in range(max_retries):
        try:
            return operation()
        except (GoogleAPICallError, RetryError, Exception) as e:
            if attempt == max_retries - 1:
                logger.error(f"Operação falhou após {max_retries} tentativas: {e}")
                raise
            logger.warning(f"Tentativa {attempt + 1} falhou, tentando novamente em {delay}s: {e}")
            time.sleep(delay * (2 ** attempt))  # Backoff exponencial

def update_document_safely(collection_ref, doc_id, update_data):
    """Atualiza um documento com tratamento de erro robusto."""
    def operation():
        return collection_ref.document(doc_id).update(update_data)
    
    return safe_firestore_operation(operation)

def get_documents_to_process(urls_ref, limit=50):
    """Busca documentos para processar com tratamento de erro."""
    def operation():
        return urls_ref.where(
            filter=FieldFilter('status', 'in', ['pending', 'reprocess'])
        ).limit(limit).stream()
    
    return safe_firestore_operation(operation)

# --- Funções de Background ---
def scrape_and_update(run_id: str):
    """
    Busca URLs, faz o scraping e atualiza o Firestore.
    Esta função é projetada para ser executada em background.
    """
    if not db:
        logger.error(f"Firestore não está disponível para a tarefa {run_id}.")
        return

    log_ref = db.collection('system_logs').document(run_id)
    processed_count = 0
    failed_count = 0
    
    try:
        urls_ref = db.collection('monitor_results')
        
        # Atualiza o log inicial
        safe_firestore_operation(lambda: log_ref.update({
            'status': 'processing',
            'message': 'Iniciando processo de scraping...'
        }))
        
        # Processa em lotes menores para evitar problemas de memória e timeout
        batch_size = 5
        total_processed = 0
        
        while total_processed < 5:  # Limite máximo de processamento
            try:
                docs_to_process = get_documents_to_process(urls_ref, batch_size)
                batch_docs = list(docs_to_process)
                
                if not batch_docs:
                    logger.info("Nenhum documento para processar encontrado")
                    break
                
                logger.info(f"Processando lote de {len(batch_docs)} documentos")
                
                for doc in batch_docs:
                    try:
                        url_data = doc.to_dict()
                        doc_id = doc.id
                        link = url_data.get('link')

                        if not link:
                            update_document_safely(urls_ref, doc_id, {
                                'status': 'scraper_failed', 
                                'error_message': 'URL não encontrada no documento.',
                                'last_processed_at': datetime.datetime.now(datetime.timezone.utc)
                            })
                            failed_count += 1
                            continue

                        domain = urlparse(link).netloc
                        if domain in SOCIAL_MEDIA_DOMAINS:
                            update_document_safely(urls_ref, doc_id, {
                                'status': 'scraper_skipped', 
                                'reason': 'Social media domain',
                                'last_processed_at': datetime.datetime.now(datetime.timezone.utc)
                            })
                            continue

                        relevance_score = calculate_relevance(url_data)
                        
                        if relevance_score < 0.50:
                            update_document_safely(urls_ref, doc_id, {
                                'status': 'relevance_failed', 
                                'relevance_score': relevance_score,
                                'last_processed_at': datetime.datetime.now(datetime.timezone.utc)
                            })
                            continue

                        # Scraping do artigo
                        try:
                            article = Article(link, language='pt')
                            article.download()
                            article.parse()

                            # Boost de relevância para artigos recentes
                            if article.publish_date and isinstance(article.publish_date, datetime.datetime):
                                if article.publish_date.replace(tzinfo=datetime.timezone.utc) > (
                                    datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=730)
                                ):
                                    relevance_score = ((relevance_score * 80) + 20) / 100

                            update_data = {
                                'status': 'scraper_ok',
                                'scraped_content': article.text[:50000],  # Limita o tamanho do conteúdo
                                'scraped_title': article.title,
                                'authors': article.authors[:10] if article.authors else [],  # Limita autores
                                'publish_date': article.publish_date,
                                'relevance_score': relevance_score,
                                'last_processed_at': datetime.datetime.now(datetime.timezone.utc)
                            }
                            
                            update_document_safely(urls_ref, doc_id, update_data)
                            processed_count += 1
                            logger.info(f"Documento {doc_id} processado com sucesso")

                        except ArticleException as e:
                            update_document_safely(urls_ref, doc_id, {
                                'status': 'scraper_failed', 
                                'error_message': f"Newspaper3k error: {str(e)[:500]}",
                                'last_processed_at': datetime.datetime.now(datetime.timezone.utc)
                            })
                            failed_count += 1
                            logger.warning(f"Erro no scraping do documento {doc_id}: {e}")
                            
                        except Exception as e:
                            update_document_safely(urls_ref, doc_id, {
                                'status': 'scraper_failed', 
                                'error_message': f"Erro inesperado: {str(e)[:500]}",
                                'last_processed_at': datetime.datetime.now(datetime.timezone.utc)
                            })
                            failed_count += 1
                            logger.error(f"Erro inesperado no documento {doc_id}: {e}")
                            
                    except Exception as doc_e:
                        logger.error(f"Erro ao processar documento: {doc_e}")
                        failed_count += 1
                        continue
                
                total_processed += len(batch_docs)
                
                # Pausa entre lotes para evitar sobrecarga
                if len(batch_docs) == batch_size:
                    time.sleep(1)
                else:
                    break  # Último lote processado
                    
            except Exception as batch_e:
                logger.error(f"Erro ao processar lote: {batch_e}")
                break
        
        # Atualiza o log final
        safe_firestore_operation(lambda: log_ref.update({
            'status': 'completed', 
            'end_time': datetime.datetime.now(datetime.timezone.utc).isoformat(), 
            'processed_count': processed_count,
            'failed_count': failed_count,
            'message': f"Processo de scraping concluído. {processed_count} URLs processadas com sucesso, {failed_count} falharam."
        }))
        
        logger.info(f"Scraping concluído: {processed_count} sucessos, {failed_count} falhas")

    except Exception as e:
        error_msg = f"Erro geral na tarefa de scraping: {str(e)}"
        logger.error(error_msg)
        try:
            safe_firestore_operation(lambda: log_ref.update({
                'status': 'failed', 
                'end_time': datetime.datetime.now(datetime.timezone.utc).isoformat(), 
                'error_message': error_msg[:1000], 
                'processed_count': processed_count,
                'failed_count': failed_count
            }))
        except Exception as log_e:
            logger.error(f"Falha ao registrar o erro no log de scraping (run_id: {run_id}): {log_e}")

# --- Endpoints ---
@app.get("/")
def read_root():
    return {"message": "Scraper Newspaper3k está no ar!", "status": "healthy" if db else "unhealthy"}

@app.get("/health")
def health_check():
    """Endpoint para verificar a saúde do serviço."""
    return {
        "status": "healthy" if db else "unhealthy",
        "firebase_connected": db is not None,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

@app.post("/scrape", status_code=status.HTTP_202_ACCEPTED)
async def trigger_scraping(background_tasks: BackgroundTasks):
    """
    Inicia o processo de scraping em background e registra um log da execução.
    """
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Conexão com o Firestore não está disponível."
        )
    
    log_entry = SystemLog(
        task='Scraping de Notícias',
        start_time=datetime.datetime.now(datetime.timezone.utc),
        status='started'
    )
    
    try:
        def create_log():
            return db.collection('system_logs').add(log_entry.model_dump())
        
        _, log_ref = safe_firestore_operation(create_log)
        run_id = log_ref.id
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Falha ao criar o log da tarefa no Firestore: {e}"
        )
    
    background_tasks.add_task(scrape_and_update, run_id)

    return {"message": "Processo de scraping em background iniciado.", "run_id": run_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
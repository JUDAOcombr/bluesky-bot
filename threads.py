import os
import requests
import feedparser
import re
import html
from datetime import datetime
from zoneinfo import ZoneInfo

# ================= CONFIGURAÇÕES =================
RSS_URL = 'https://newsletter.judao.com.br/rss'
POSTED_FILE = 'posted_urls.txt'

THREADS_USER_ID = os.getenv('THREADS_USER_ID')
THREADS_TOKEN = os.getenv('THREADS_TOKEN')
# =================================================

def is_time_allowed():
    tz_brasilia = ZoneInfo("America/Sao_Paulo")
    hour = datetime.now(tz_brasilia).hour
    print(f"Hora atual em Brasília: {hour}h")
    return 10 <= hour < 22

def get_posted_urls():
    if not os.path.exists(POSTED_FILE):
        return []
    with open(POSTED_FILE, 'r') as f:
        return f.read().splitlines()

def save_posted_url(url):
    with open(POSTED_FILE, 'a') as f:
        f.write(url + '\n')

def clean_html_and_unescape(raw_html):
    cleanr = re.compile('<.*?>')
    cleaned_text = re.sub(cleanr, '', raw_html).strip()
    return html.unescape(cleaned_text)

def post_to_threads(title, short_desc, url):
    """Executa a postagem no Threads via API da Meta"""
    print("\n--- Iniciando postagem no Threads ---")
    try:
        post_text = f"{title}\n\n{short_desc}\n\n🔗 {url}"
        
        # Passo 1: Criar o rascunho
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        create_payload = {'media_type': 'TEXT', 'text': post_text, 'access_token': THREADS_TOKEN}
        response = requests.post(create_url, data=create_payload).json()

        if 'id' not in response:
            print(f"Erro no rascunho do Threads: {response}")
            return False

        container_id = response['id']

        # Passo 2: Publicar
        publish_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        publish_payload = {'creation_id': container_id, 'access_token': THREADS_TOKEN}
        pub_response = requests.post(publish_url, data=publish_payload).json()

        if 'id' in pub_response:
            print("Post enviado com sucesso para o Threads!")
            return True
        else:
            print(f"Erro na publicação do Threads: {pub_response}")
            return False
    except Exception as e:
        print(f"Erro ao postar no Threads: {e}")
        return False

def main():
    if not all([THREADS_USER_ID, THREADS_TOKEN]):
        print("Erro: Faltam as credenciais do Threads nos Secrets do GitHub.")
        return

    if not is_time_allowed():
        print("Fora do horário permitido (10h às 22h). Script encerrado.")
        return

    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        print("Nenhum post encontrado no RSS.")
        return

    latest_entry = feed.entries[0]
    url = latest_entry.link

    posted = get_posted_urls()
    if url in posted:
        print("O post mais recente já foi publicado anteriormente.")
        return

    print(f"Novo post detectado: {latest_entry.title}")

    # Processa os textos
    title = html.unescape(latest_entry.title)
    description = clean_html_and_unescape(latest_entry.get('summary', 'Sem descrição'))
    short_desc = description[:240] + "..." if len(description) > 240 else description

    # Dispara para o Threads
    sucesso_threads = post_to_threads(title, short_desc, url)

    if sucesso_threads:
        save_posted_url(url)
        print("\nHistórico atualizado com sucesso!")

if __name__ == '__main__':
    main()

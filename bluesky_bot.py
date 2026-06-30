import feedparser
import requests
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from atproto import Client, client_utils, models

# ================= CONFIGURAÇÕES =================
RSS_URL = 'https://newsletter.judao.com.br/feed'
POSTED_FILE = 'posted_urls.txt'

# Pegando as credenciais seguras do GitHub
BSKY_HANDLE = os.getenv('BSKY_HANDLE')
BSKY_PASSWORD = os.getenv('BSKY_PASSWORD')
# =================================================

def is_time_allowed():
    """Verifica se o horário atual de Brasília está entre 10h00 e 22h00."""
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

def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html).strip()

def main():
    if not BSKY_HANDLE or not BSKY_PASSWORD:
        print("Erro: Credenciais BSKY_HANDLE ou BSKY_PASSWORD não encontradas nos Secrets.")
        return

    # 1. Verifica janela de horário
    if not is_time_allowed():
        print("Fora do horário permitido (10h às 22h). Script encerrado.")
        return

    # 2. Lê o RSS
    feed = feedparser.parse(RSS_URL)
    if not feed.entries:
        print("Nenhum post encontrado no RSS.")
        return

    latest_entry = feed.entries[0]
    url = latest_entry.link

    # 3. Verifica duplicata
    posted = get_posted_urls()
    if url in posted:
        print("O post mais recente já foi publicado anteriormente.")
        return

    print(f"Novo post detectado: {latest_entry.title}")

    title = latest_entry.title
    raw_description = latest_entry.get('summary', 'Sem descrição')
    description = clean_html(raw_description)
    description = description[:250] + "..." if len(description) > 250 else description

    image_url = None
    if 'media_content' in latest_entry:
        image_url = latest_entry.media_content[0]['url']
    elif 'enclosures' in latest_entry and len(latest_entry.enclosures) > 0:
        image_url = latest_entry.enclosures[0].href

    # 4. Login no Bluesky
    client = Client()
    client.login(BSKY_HANDLE, BSKY_PASSWORD)

    # 5. POST 1 (Título + URL + Imagem)
    tb1 = client_utils.TextBuilder()
    tb1.text(f"{title}\n\n")
    tb1.link(url, url)

    embed = None
    if image_url:
        print(f"Baixando imagem: {image_url}")
        img_req = requests.get(image_url)
        if img_req.status_code == 200:
            image_blob = client.upload_blob(img_req.content).blob
            embed = models.AppBskyEmbedImages.Main(
                images=[models.AppBskyEmbedImages.Image(alt=title, image=image_blob)]
            )

    post1 = client.send_post(text=tb1, embed=embed)
    print("Post 1 enviado.")

    # 6. POST 2 (Thread: Descrição + URL)
    tb2 = client_utils.TextBuilder()
    tb2.text(f"{description}\n\n")
    tb2.link(url, url)

    root = models.create_strong_ref(post1)
    parent = models.create_strong_ref(post1)
    reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent, root=root)

    client.send_post(text=tb2, reply_to=reply_ref)
    print("Post 2 (Thread) enviado.")

    # 7. Salva a URL para não repetir
    save_posted_url(url)
    print("Histórico atualizado localmente.")

if __name__ == '__main__':
    main()

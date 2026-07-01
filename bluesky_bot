import feedparser
import requests
import os
import re
import html
from datetime import datetime
from zoneinfo import ZoneInfo
from atproto import Client, client_utils, models

# ================= CONFIGURAÇÕES =================
RSS_URL = 'https://newsletter.judao.com.br/feed'
POSTED_FILE = 'posted_urls.txt'

BSKY_HANDLE = os.getenv('BSKY_HANDLE')
BSKY_PASSWORD = os.getenv('BSKY_PASSWORD')
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
    """Remove as tags HTML e converte códigos como &#227; para acentos reais."""
    cleanr = re.compile('<.*?>')
    cleaned_text = re.sub(cleanr, '', raw_html).strip()
    return html.unescape(cleaned_text)

def extract_image_urls(entry):
    """Vasculha o RSS para encontrar todas as imagens possíveis e retorna uma lista."""
    urls = []
    
    # Tenta procurar na tag media_content
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'url' in media and media['url'] not in urls:
                urls.append(media['url'])
                
    # Procura dentro do HTML principal do post usando Regex
    html_content = entry.get('content', [{'value': entry.get('summary', '')}])[0]['value']
    img_tags = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html_content)
    for img in img_tags:
        if img not in urls:
            urls.append(img)
            
    return urls

def main():
    if not BSKY_HANDLE or not BSKY_PASSWORD:
        print("Erro: Credenciais não encontradas nos Secrets.")
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

    # Extrai o Título e a Descrição (com a correção de acentuação)
    title = html.unescape(latest_entry.title)
    raw_description = latest_entry.get('summary', 'Sem descrição')
    description = clean_html_and_unescape(raw_description)
    
    # Limita o tamanho para o Bluesky não dar erro (max 300 caracteres)
    short_desc = description[:250] + "..." if len(description) > 250 else description

    # Extrai as imagens (Vamos pegar no máximo as 2 primeiras)
    all_images = extract_image_urls(latest_entry)
    images_to_post = all_images[:2]

    # Inicia Login
    client = Client()
    client.login(BSKY_HANDLE, BSKY_PASSWORD)

    # ---------------------------------------------------------
    # POST 1: Descrição + URL + 2 Imagens Embedadas
    # ---------------------------------------------------------
    tb1 = client_utils.TextBuilder()
    tb1.text(f"{short_desc}\n\n")
    tb1.link(url, url)

    # Baixa e prepara as imagens para o Post 1
    image_blobs = []
    bsky_images = []
    
    for img_url in images_to_post:
        print(f"Baixando imagem: {img_url}")
        img_req = requests.get(img_url)
        if img_req.status_code == 200:
            blob = client.upload_blob(img_req.content).blob
            image_blobs.append(blob)
            bsky_images.append(models.AppBskyEmbedImages.Image(alt=title, image=blob))

    embed1 = models.AppBskyEmbedImages.Main(images=bsky_images) if bsky_images else None

    post1 = client.send_post(text=tb1, embed=embed1)
    print("Post 1 enviado (Descrição + Imagens).")

    # ---------------------------------------------------------
    # POST 2 (Thread): Texto Fixo + CARD da URL
    # ---------------------------------------------------------
    tb2 = client_utils.TextBuilder()
    tb2.text("Se inscreva e leia na SUA 🫵 caixa de entrada!")

    # Para o CARD, podemos reutilizar a primeira imagem como miniatura (se existir)
    card_thumb = image_blobs[0] if image_blobs else None

    # Cria o formato CARD Externo
    embed2 = models.AppBskyEmbedExternal.Main(
        external=models.AppBskyEmbedExternal.External(
            title=title,
            description=short_desc,
            uri=url,
            thumb=card_thumb
        )
    )

    # Linka o Post 2 como resposta ao Post 1
    root = models.create_strong_ref(post1)
    parent = models.create_strong_ref(post1)
    reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent, root=root)

    client.send_post(text=tb2, embed=embed2, reply_to=reply_ref)
    print("Post 2 enviado (Thread com Card).")

    # Salva no histórico local
    save_posted_url(url)
    print("Histórico atualizado localmente.")

if __name__ == '__main__':
    main()

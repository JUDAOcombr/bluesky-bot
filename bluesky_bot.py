import os
import requests
import feedparser
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

def extract_image_urls(entry):
    urls = []
    if 'media_content' in entry:
        for media in entry.media_content:
            if 'url' in media and media['url'] not in urls:
                urls.append(media['url'])
    html_content = entry.get('content', [{'value': entry.get('summary', '')}])[0]['value']
    img_tags = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html_content)
    for img in img_tags:
        if img not in urls:
            urls.append(img)
    return urls

def post_to_bluesky(title, short_desc, url, images_to_post):
    print("\n--- Iniciando postagem no Bluesky ---")
    try:
        client = Client()
        client.login(BSKY_HANDLE, BSKY_PASSWORD)

        # Post 1 (Descrição + Link + Imagens)
        tb1 = client_utils.TextBuilder()
        tb1.text(f"{short_desc}\n\n")
        tb1.link(url, url)

        image_blobs = []
        bsky_images = []
        for img_url in images_to_post:
            img_req = requests.get(img_url)
            if img_req.status_code == 200:
                blob = client.upload_blob(img_req.content).blob
                image_blobs.append(blob)
                bsky_images.append(models.AppBskyEmbedImages.Image(alt=title, image=blob))

        embed1 = models.AppBskyEmbedImages.Main(images=bsky_images) if bsky_images else None
        post1 = client.send_post(text=tb1, embed=embed1)
        print("Post 1 enviado para o Bluesky.")

        # Post 2 (Texto Fixo + Card)
        tb2 = client_utils.TextBuilder()
        tb2.text("Se inscreva e leia na SUA 🫵 caixa de entrada!")
        
        card_thumb = image_blobs[0] if image_blobs else None
        embed2 = models.AppBskyEmbedExternal.Main(
            external=models.AppBskyEmbedExternal.External(
                title=title, description=short_desc, uri=url, thumb=card_thumb
            )
        )

        root = models.create_strong_ref(post1)
        parent = models.create_strong_ref(post1)
        reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent, root=root)

        client.send_post(text=tb2, embed=embed2, reply_to=reply_ref)
        print("Post 2 (Thread) enviado para o Bluesky.")
        return True
    except Exception as e:
        print(f"Erro ao postar no Bluesky: {e}")
        return False

def post_to_threads(title, short_desc, url):
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
    if not all([BSKY_HANDLE, BSKY_PASSWORD, THREADS_USER_ID, THREADS_TOKEN]):
        print("Erro: Faltam credenciais nos Secrets do GitHub (Verifique se preencheu as 4).")
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

    # Coleta as imagens para o Bluesky
    all_images = extract_image_urls(latest_entry)
    images_to_post = all_images[:2]

    # Dispara em paralelo para as duas redes
    sucesso_bsky = post_to_bluesky(title, short_desc, url, images_to_post)
    sucesso_threads = post_to_threads(title, short_desc, url)

    # Só salva no histórico se pelo menos uma postagem der certo
    if sucesso_bsky or sucesso_threads:
        save_posted_url(url)
        print("\nHistórico atualizado com sucesso!")

if __name__ == '__main__':
    main()

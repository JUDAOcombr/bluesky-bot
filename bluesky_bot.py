import os
import time
import requests
import feedparser
import re
import html
import urllib.parse
import io
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
from atproto import Client, client_utils, models
from PIL import Image

# ================= CONFIGURAÇÕES =================
RSS_URL = 'https://newsletter.judao.com.br/feed'

# Históricos separados por rede
POSTED_BSKY_FILE = 'posted_urls_bluesky.txt'
POSTED_THREADS_FILE = 'posted_urls_threads.txt'

BSKY_HANDLE = os.getenv('BSKY_HANDLE')
BSKY_PASSWORD = os.getenv('BSKY_PASSWORD')

THREADS_USER_ID = os.getenv('THREADS_USER_ID')
THREADS_TOKEN = os.getenv('THREADS_TOKEN')

# Opcionais, usados apenas para checagem avançada do token do Threads
THREADS_APP_ID = os.getenv('THREADS_APP_ID')
THREADS_APP_SECRET = os.getenv('THREADS_APP_SECRET')

REQUEST_TIMEOUT = 30
THREADS_PROCESSING_WAIT = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JUDAO-Social-Bot/1.0)"
}
# =================================================


def is_time_allowed():
    tz_brasilia = ZoneInfo("America/Sao_Paulo")
    hour = datetime.now(tz_brasilia).hour
    print(f"Hora atual em Brasília: {hour}h")
    return 10 <= hour < 22


def get_posted_urls(file_path):
    """Carrega URLs já publicadas em um histórico específico."""
    if not os.path.exists(file_path):
        return set()

    with open(file_path, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())


def save_posted_url(file_path, url):
    """Salva uma URL em um histórico específico, sem duplicar."""
    posted = get_posted_urls(file_path)

    if url in posted:
        print(f"URL já existe em {file_path}. Histórico não alterado.")
        return

    with open(file_path, 'a', encoding='utf-8') as f:
        f.write(url + '\n')


def clean_html_and_unescape(raw_html):
    cleanr = re.compile('<.*?>')
    cleaned_text = re.sub(cleanr, '', raw_html).strip()
    return html.unescape(cleaned_text)


def normalize_image_url(img_url, base_url):
    """Normaliza URL absoluta/relativa e remove escapes HTML."""
    if not img_url:
        return None

    img_url = html.unescape(img_url).strip()

    if not img_url:
        return None

    if img_url.startswith("data:"):
        return None

    return urljoin(base_url, img_url)


def is_probably_valid_image_url(img_url):
    """Filtra logos, pixels e previews genéricos do Substack que duplicam a capa."""
    if not img_url:
        return False

    decoded = urllib.parse.unquote(img_url.lower())

    blocked_terms = [
        "avatar", "logo", "icon", "favicon", "sprite", "pixel", "tracking",
        "spacer", "blank", "transparent", "gravatar", 
        "post_preview", "twitter.jpg", "twitter_card", "og.jpg"
    ]

    if any(term in decoded for term in blocked_terms):
        return False

    allowed_extensions = [".jpg", ".jpeg", ".png", ".webp", ".gif"]
    if any(ext in decoded for ext in allowed_extensions):
        return True

    if "image" in decoded or "upload" in decoded or "cdn" in decoded or "substack" in decoded:
        return True

    return True


def force_substack_image_size(img_url):
    """HACK GENIAL: Pede para o servidor do Substack entregar a imagem já redimensionada para 1456 pixels."""
    if not img_url or "substackcdn.com" not in img_url:
        return img_url
    if re.search(r'w_\d+', img_url):
        return re.sub(r'w_\d+', 'w_1456', img_url)
    else:
        return img_url.replace('/fetch/', '/fetch/w_1456,c_limit,')


def extract_image_urls(entry, article_url):
    """Pega APENAS as imagens que estão dentro do corpo do texto da newsletter, na ordem."""
    urls = []
    
    html_content = ""
    if 'content' in entry:
        for content_item in entry.content:
            html_content += content_item.get('value', '')
    elif 'summary' in entry:
        html_content += entry.summary

    img_tags = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html_content, flags=re.IGNORECASE)

    for img_url in img_tags:
        normalized = normalize_image_url(img_url, article_url)
        if normalized and is_probably_valid_image_url(normalized):
            optimized = force_substack_image_size(normalized)
            if optimized not in urls:
                urls.append(optimized)

    if not urls and 'media_content' in entry:
        for media in entry.media_content:
            media_url = media.get('url')
            normalized = normalize_image_url(media_url, article_url)
            if normalized and is_probably_valid_image_url(normalized):
                optimized = force_substack_image_size(normalized)
                if optimized not in urls:
                    urls.append(optimized)

    print(f"Imagens limpas encontradas: {len(urls)}")
    return urls


def checar_threads_basico():
    if not THREADS_USER_ID or not THREADS_TOKEN:
        print("Threads não configurado.")
        return False

    print("\n--- Checando configuração básica do Threads ---")
    check_url = "https://graph.threads.net/v1.0/me"
    params = {"fields": "id,username", "access_token": THREADS_TOKEN}

    try:
        response = requests.get(check_url, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            print("Falha na checagem básica do Threads.")
            return False

        data = response.json()
        if str(data.get("id")) != str(THREADS_USER_ID):
            print("Atenção: THREADS_USER_ID não bate com o token.")
            return False

        print(f"Threads básico OK: token responde para @{data.get('username')}.")
        return True
    except Exception as e:
        print(f"Erro ao checar Threads: {e}")
        return False


def checar_threads_token_avancado():
    if not THREADS_APP_ID or not THREADS_APP_SECRET:
        return None

    print("\n--- Checando token do Threads via debug_token ---")
    app_access_token = f"{THREADS_APP_ID}|{THREADS_APP_SECRET}"
    debug_url = "https://graph.facebook.com/debug_token"
    params = {"input_token": THREADS_TOKEN, "access_token": app_access_token}

    try:
        response = requests.get(debug_url, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return None
        return response.json().get("data", {})
    except Exception as e:
        print(f"Erro ao executar debug_token: {e}")
        return None


def download_image_for_bluesky(img_url):
    """Baixa a imagem. O CDN do Substack já a entrega leve a 1456px."""
    try:
        img_req = requests.get(img_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)

        if img_req.status_code != 200:
            print(f"Imagem ignorada no Bluesky. HTTP {img_req.status_code}")
            return None

        content_type = img_req.headers.get("content-type", "")
        if content_type and not content_type.startswith("image/"):
            print("URL ignorada no Bluesky: não parece ser imagem.")
            return None

        return img_req.content

    except Exception as e:
        print(f"Erro ao baixar imagem para o Bluesky: {e}")
        return None


def get_image_dimensions(image_bytes):
    """Lê cirurgicamente largura e altura da imagem usando Pillow."""
    if not image_bytes:
        return None, None
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return img.size # Retorna (width, height)
    except Exception as e:
        print(f"Erro ao ler dimensões da imagem: {e}")
        return None, None


def post_to_bluesky(title, short_desc, url, images_to_post):
    print("\n--- Iniciando postagem no Bluesky ---")

    if not BSKY_HANDLE or not BSKY_PASSWORD:
        print("Bluesky não configurado.")
        return False

    try:
        client = Client()
        client.login(BSKY_HANDLE, BSKY_PASSWORD)

        # Post 1: texto + link inserido nativamente + até duas primeiras imagens
        tb1 = client_utils.TextBuilder()
        tb1.text(f"{short_desc}\n\n")
        tb1.link(url, url)

        bsky_images = []
        image_blobs = []

        for img_url in images_to_post[:2]:
            image_bytes = download_image_for_bluesky(img_url)
            if not image_bytes:
                continue
            
            # Lê as dimensões (largura, altura)
            width, height = get_image_dimensions(image_bytes)
            
            try:
                blob = client.upload_blob(image_bytes).blob
                image_blobs.append(blob)
                
                bsky_images.append(
                    models.AppBskyEmbedImages.Image(
                        alt=title, 
                        image=blob,
                        aspect_ratio=models.AppBskyEmbedDefs.AspectRatio(
                            width=width if width else 1,
                            height=height if height else 1
                        )
                    )
                )
                print(f"Upload concluído ({width}x{height}): {img_url}")
            except Exception as e:
                print(f"Erro ao enviar imagem ao Bluesky: {e}")

        embed1 = models.AppBskyEmbedImages.Main(images=bsky_images) if bsky_images else None

        post1 = client.send_post(text=tb1, embed=embed1)
        print(f"Post 1 enviado para o Bluesky com {len(bsky_images)} imagem(ns) no formato original.")

        # Post 2: resposta com chamada + link/card
        tb2 = client_utils.TextBuilder()
        tb2.text("Se inscreva e leia na SUA 🫵 caixa de entrada!\n\n")
        tb2.link(url, url)

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
        print("Post 2 com link/card enviado para o Bluesky.")
        return True

    except Exception as e:
        print(f"Erro ao postar no Bluesky: {e}")
        return False


def criar_container_threads(payload):
    create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
    response = requests.post(create_url, data=payload, timeout=REQUEST_TIMEOUT)
    try:
        data = response.json()
        if response.status_code != 200 or 'id' not in data:
            return None
        return data['id']
    except Exception:
        return None


def publicar_container_threads(container_id):
    publish_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
    payload = {'creation_id': container_id, 'access_token': THREADS_TOKEN}
    response = requests.post(publish_url, data=payload, timeout=REQUEST_TIMEOUT)
    try:
        data = response.json()
        if response.status_code == 200 and 'id' in data:
            return data['id']
        return None
    except Exception:
        return None


def criar_primeiro_post_threads_com_imagens(short_desc, images_to_post):
    images = images_to_post[:2]

    if len(images) >= 2:
        print("Threads: criando carrossel com 2 imagens.")
        child_container_ids = []

        for img_url in images:
            child_payload = {
                'media_type': 'IMAGE', 'image_url': img_url, 
                'is_carousel_item': 'true', 'access_token': THREADS_TOKEN
            }
            child_id = criar_container_threads(child_payload)
            if not child_id:
                return None
            child_container_ids.append(child_id)
            print(f"Threads: container criado para imagem: {img_url}")

        print(f"Threads: aguardando {THREADS_PROCESSING_WAIT}s para processamento...")
        time.sleep(THREADS_PROCESSING_WAIT)

        carousel_payload = {
            'media_type': 'CAROUSEL', 'children': ','.join(child_container_ids),
            'text': short_desc, 'access_token': THREADS_TOKEN
        }
        carousel_container_id = criar_container_threads(carousel_payload)
        if not carousel_container_id:
            return None

        print(f"Threads: carrossel montado. Aguardando {THREADS_PROCESSING_WAIT}s...")
        time.sleep(THREADS_PROCESSING_WAIT)

        return publicar_container_threads(carousel_container_id)

    if len(images) == 1:
        print("Threads: criando post com 1 imagem.")
        image_payload = {
            'media_type': 'IMAGE', 'image_url': images[0],
            'text': short_desc, 'access_token': THREADS_TOKEN
        }
        image_container_id = criar_container_threads(image_payload)
        if not image_container_id:
            return None

        print(f"Threads: post com imagem criado. Aguardando {THREADS_PROCESSING_WAIT}s...")
        time.sleep(THREADS_PROCESSING_WAIT)
        return publicar_container_threads(image_container_id)

    print("Threads: nenhuma imagem encontrada. Criando primeiro post apenas com texto.")
    text_payload = {
        'media_type': 'TEXT', 'text': short_desc, 'access_token': THREADS_TOKEN
    }
    text_container_id = criar_container_threads(text_payload)
    if not text_container_id:
        return None

    print(f"Threads: post de texto criado. Aguardando {THREADS_PROCESSING_WAIT}s...")
    time.sleep(THREADS_PROCESSING_WAIT)
    return publicar_container_threads(text_container_id)


def criar_segundo_post_threads_com_link_preview(url, reply_to_id):
    second_text = f"Se inscreva e leia na SUA 🫵 caixa de entrada!\n\n🔗 {url}"
    reply_payload = {
        'media_type': 'TEXT', 'text': second_text, 'reply_to_id': reply_to_id,
        'link_attachment': url, 'access_token': THREADS_TOKEN
    }
    reply_container_id = criar_container_threads(reply_payload)
    if not reply_container_id:
        return None

    print(f"Threads: segundo post com link preview criado. Aguardando {THREADS_PROCESSING_WAIT}s...")
    time.sleep(THREADS_PROCESSING_WAIT)
    return publicar_container_threads(reply_container_id)


def post_to_threads(title, short_desc, url, images_to_post):
    print("\n--- Iniciando postagem no Threads ---")

    if not THREADS_USER_ID or not THREADS_TOKEN:
        print("Threads não configurado.")
        return False

    try:
        threads_first_text = f"{short_desc}\n\n🔗 {url}"
        first_post_id = criar_primeiro_post_threads_com_imagens(threads_first_text, images_to_post)
        if not first_post_id:
            return False

        print(f"Threads: primeiro post publicado com sucesso. ID: {first_post_id}")
        second_post_id = criar_segundo_post_threads_com_link_preview(url, first_post_id)
        if not second_post_id:
            return False

        print(f"Threads: segundo post com link preview publicado com sucesso. ID: {second_post_id}")
        return True

    except Exception as e:
        print(f"Erro ao postar no Threads: {e}")
        return False


def main():
    bsky_config_ok = bool(BSKY_HANDLE and BSKY_PASSWORD)
    threads_config_ok = bool(THREADS_USER_ID and THREADS_TOKEN)

    if not bsky_config_ok and not threads_config_ok:
        print("Erro: nenhuma rede está configurada.")
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

    print(f"Post mais recente no RSS: {latest_entry.title}")
    print(f"URL: {url}")

    posted_bsky = get_posted_urls(POSTED_BSKY_FILE)
    posted_threads = get_posted_urls(POSTED_THREADS_FILE)

    already_bsky = url in posted_bsky
    already_threads = url in posted_threads

    if already_bsky and already_threads:
        print("O post mais recente já foi publicado anteriormente no Bluesky e no Threads.")
        return 

    title = html.unescape(latest_entry.title)
    description = clean_html_and_unescape(latest_entry.get('summary', 'Sem descrição'))
    short_desc = description[:240] + "..." if len(description) > 240 else description

    all_images = extract_image_urls(latest_entry, url)
    images_to_post = all_images[:2]

    print(f"Imagens selecionadas para postagem: {len(images_to_post)}")

    sucesso_bsky = False
    sucesso_threads = False

    # ================= BLUESKY =================
    if already_bsky:
        print("\nBluesky: post mais recente já publicado.")
    elif not bsky_config_ok:
        print("\nBluesky pulado: credenciais ausentes.")
    else:
        sucesso_bsky = post_to_bluesky(title, short_desc, url, images_to_post)
        if sucesso_bsky:
            save_posted_url(POSTED_BSKY_FILE, url)
            print("Histórico do Bluesky atualizado.")

    # ================= THREADS =================
    if already_threads:
        print("\nThreads: post mais recente já publicado.")
    elif not threads_config_ok:
        print("\nThreads pulado: credenciais ausentes.")
    else:
        checar_threads_basico()
        checar_threads_token_avancado()
        sucesso_threads = post_to_threads(title, short_desc, url, images_to_post)
        if sucesso_threads:
            save_posted_url(POSTED_THREADS_FILE, url)
            print("Histórico do Threads atualizado.")

    # ================= RESUMO =================
    print("\nResumo da execução:")
    print(f"Bluesky: {sucesso_bsky}")
    print(f"Threads: {sucesso_threads}")


if __name__ == '__main__':
    main()

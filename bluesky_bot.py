import os
import time
import requests
import feedparser
import re
import html
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
from atproto import Client, client_utils, models
from io import BytesIO
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
    """Filtra logos, pixels e assets que não parecem ser imagens editoriais do post."""
    if not img_url:
        return False

    lowered = img_url.lower()

    blocked_terms = [
        "avatar",
        "logo",
        "icon",
        "favicon",
        "sprite",
        "pixel",
        "tracking",
        "spacer",
        "blank",
        "transparent",
        "gravatar",
    ]

    if any(term in lowered for term in blocked_terms):
        return False

    allowed_extensions = [".jpg", ".jpeg", ".png", ".webp", ".gif"]

    # Aceita URLs com extensão clara ou URLs de CDN com parâmetros.
    if any(ext in lowered for ext in allowed_extensions):
        return True

    # Muitos CDNs não terminam a URL com extensão, então não bloqueamos agressivamente.
    if "image" in lowered or "upload" in lowered or "cdn" in lowered or "substack" in lowered:
        return True

    return True


def get_core_image_name(img_url):
    """
    Extrai apenas o nome final do arquivo de imagem (ex: e10d5e23..._6000x3380.jpeg).
    Isso ignora qualquer CDN, redimensionamento ou parâmetro maluco do Substack no meio do link.
    """
    if not img_url:
        return ""
    
    # 1. Decodifica a URL (transforma códigos como %2F em barras reais /)
    decoded = urllib.parse.unquote(img_url)
    
    # 2. Corta qualquer coisa que vier depois de um '?' 
    no_query = decoded.split('?')[0]
    
    # 3. Pega absolutamente a última palavra depois da última barra '/' (o nome real do arquivo)
    filename = no_query.strip('/').split('/')[-1]
    
    return filename


def deduplicate_images(urls):
    """Filtra a lista de imagens mantendo apenas imagens únicas baseadas no nome real do arquivo."""
    unique_urls = []
    seen_names = set()
    for u in urls:
        core_name = get_core_image_name(u)
        if core_name and core_name not in seen_names:
            seen_names.add(core_name)
            unique_urls.append(u)
    return unique_urls


def extract_srcset_urls(srcset_value, base_url):
    """Extrai URLs de atributos srcset/data-srcset."""
    urls = []

    if not srcset_value:
        return urls

    parts = srcset_value.split(",")

    for part in parts:
        candidate = part.strip().split(" ")[0]
        normalized = normalize_image_url(candidate, base_url)

        if normalized and is_probably_valid_image_url(normalized):
            urls.append(normalized)

    return urls


def extract_image_urls_from_html(html_content, base_url):
    """Extrai imagens de um HTML usando src, data-src, srcset, data-srcset e og:image."""
    urls = []

    if not html_content:
        return urls

    # og:image / twitter:image
    meta_patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ]

    for pattern in meta_patterns:
        for match in re.findall(pattern, html_content, flags=re.IGNORECASE):
            normalized = normalize_image_url(match, base_url)
            if normalized and is_probably_valid_image_url(normalized) and normalized not in urls:
                urls.append(normalized)

    # <img ...>
    img_tags = re.findall(r'<img[^>]*>', html_content, flags=re.IGNORECASE)

    for tag in img_tags:
        attr_patterns = [
            r'\ssrc=["\']([^"\']+)["\']',
            r'\sdata-src=["\']([^"\']+)["\']',
            r'\sdata-original=["\']([^"\']+)["\']',
            r'\sdata-lazy-src=["\']([^"\']+)["\']',
        ]

        for pattern in attr_patterns:
            for match in re.findall(pattern, tag, flags=re.IGNORECASE):
                normalized = normalize_image_url(match, base_url)
                if normalized and is_probably_valid_image_url(normalized) and normalized not in urls:
                    urls.append(normalized)

        srcset_patterns = [
            r'\ssrcset=["\']([^"\']+)["\']',
            r'\sdata-srcset=["\']([^"\']+)["\']',
        ]

        for pattern in srcset_patterns:
            for srcset_value in re.findall(pattern, tag, flags=re.IGNORECASE):
                for srcset_url in extract_srcset_urls(srcset_value, base_url):
                    if srcset_url not in urls:
                        urls.append(srcset_url)

    return urls


def extract_image_urls(entry, article_url):
    """
    Pega APENAS as imagens que estão dentro do corpo do texto da newsletter,
    exatamente na ordem em que aparecem.
    """
    urls = []
    
    # 1. Junta o texto completo do post que vem no RSS
    html_content = ""
    if 'content' in entry:
        for content_item in entry.content:
            html_content += content_item.get('value', '')
    elif 'summary' in entry:
        html_content += entry.summary

    # 2. Busca cirurgicamente as tags <img src="..."> dentro do texto
    img_tags = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html_content, flags=re.IGNORECASE)

    for img_url in img_tags:
        normalized = normalize_image_url(img_url, article_url)
        # O filtro de lixo continua ativo para barrar pixels de rastreamento
        if normalized and is_probably_valid_image_url(normalized):
            # Garante que não vai adicionar a mesmíssima URL duas vezes
            if normalized not in urls:
                urls.append(normalized)

    # 3. Fallback de segurança (caso o texto venha vazio, pega a capa oficial do RSS)
    if not urls and 'media_content' in entry:
        for media in entry.media_content:
            media_url = media.get('url')
            normalized = normalize_image_url(media_url, article_url)
            if normalized and is_probably_valid_image_url(normalized) and normalized not in urls:
                urls.append(normalized)

    print(f"Imagens limpas encontradas no corpo do texto: {len(urls)}")
    return urls

    # 2. Imagens da página real do post
    try:
        response = requests.get(article_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)

        if response.status_code == 200:
            page_images = extract_image_urls_from_html(response.text, article_url)
            added = 0

            for img_url in page_images:
                if img_url not in urls:
                    urls.append(img_url)
                    added += 1

            print(f"Imagens brutas adicionais da página do post: {added}")
        else:
            print(f"Não foi possível abrir a página do post para extrair imagens. HTTP {response.status_code}")

    except Exception as e:
        print(f"Erro ao abrir página do post para extrair imagens: {e}")

    # Passa o filtro final para não postar fotos idênticas
    return deduplicate_images(urls)


def checar_threads_basico():
    if not THREADS_USER_ID or not THREADS_TOKEN:
        print("Threads não configurado: THREADS_USER_ID ou THREADS_TOKEN ausente.")
        return False

    print("\n--- Checando configuração básica do Threads ---")
    check_url = "https://graph.threads.net/v1.0/me"
    params = {
        "fields": "id,username",
        "access_token": THREADS_TOKEN,
    }

    try:
        response = requests.get(check_url, params=params, timeout=REQUEST_TIMEOUT)
        try:
            data = response.json()
        except Exception:
            print("Falha ao interpretar resposta da checagem básica do Threads.")
            return False

        if response.status_code != 200:
            print("Falha na checagem básica do Threads:")
            print(data)
            return False

        returned_id = str(data.get("id"))
        expected_id = str(THREADS_USER_ID)

        if returned_id != expected_id:
            print("Atenção: THREADS_USER_ID não bate com o token.")
            return False

        print(f"Threads básico OK: token responde para @{data.get('username')}.")
        return True

    except Exception as e:
        print(f"Erro ao checar Threads: {e}")
        return False


def checar_threads_token_avancado():
    if not THREADS_APP_ID or not THREADS_APP_SECRET:
        print("Checagem avançada do Threads pulada: THREADS_APP_ID ou THREADS_APP_SECRET ausente.")
        return None

    print("\n--- Checando token do Threads via debug_token ---")
    app_access_token = f"{THREADS_APP_ID}|{THREADS_APP_SECRET}"
    debug_url = "https://graph.facebook.com/debug_token"
    params = {
        "input_token": THREADS_TOKEN,
        "access_token": app_access_token,
    }

    try:
        response = requests.get(debug_url, params=params, timeout=REQUEST_TIMEOUT)
        try:
            data = response.json()
        except Exception:
            print("Falha ao interpretar resposta do debug_token.")
            return None

        if response.status_code != 200:
            print("Falha no debug_token:")
            return None

        token_data = data.get("data", {})
        scopes = token_data.get("scopes", []) or []

        print("Resultado do debug_token:")
        print(f"Token válido: {token_data.get('is_valid')}")
        print(f"Scopes: {scopes}")

        return token_data

    except Exception as e:
        print(f"Erro ao executar debug_token: {e}")
        return None


def download_image_for_bluesky(img_url):
    """Baixa a imagem e redimensiona/comprime automaticamente se passar de 1.5MB."""
    try:
        img_req = requests.get(img_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)

        if img_req.status_code != 200:
            print(f"Imagem ignorada no Bluesky. HTTP {img_req.status_code}: {img_url}")
            return None

        content_type = img_req.headers.get("content-type", "")

        if content_type and not content_type.startswith("image/"):
            print(f"URL ignorada no Bluesky: não parece ser imagem.")
            return None

        image_bytes = img_req.content
        
        # Limite do Bluesky é 2MB. Comprimimos se passar de 1.5MB (1500000 bytes)
        if len(image_bytes) > 1500000:
            print(f"Imagem muito grande ({len(image_bytes)} bytes). Otimizando...")
            try:
                img = Image.open(BytesIO(image_bytes))
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                output = BytesIO()
                img.thumbnail((1200, 1200))
                img.save(output, format="JPEG", quality=85)
                image_bytes = output.getvalue()
                print(f"Imagem otimizada com sucesso para {len(image_bytes)} bytes.")
            except Exception as e:
                print(f"Falha ao otimizar imagem, tentando enviar a original. Erro: {e}")

        return image_bytes

    except Exception as e:
        print(f"Erro ao baixar imagem para o Bluesky: {e}")
        return None


def post_to_bluesky(title, short_desc, url, images_to_post):
    print("\n--- Iniciando postagem no Bluesky ---")

    if not BSKY_HANDLE or not BSKY_PASSWORD:
        print("Bluesky não configurado: BSKY_HANDLE ou BSKY_PASSWORD ausente.")
        return False

    try:
        client = Client()
        client.login(BSKY_HANDLE, BSKY_PASSWORD)

        tb1 = client_utils.TextBuilder()
        tb1.text(f"{short_desc}\n\n")
        tb1.link(url, url)

        bsky_images = []
        image_blobs = []

        for img_url in images_to_post[:2]:
            image_bytes = download_image_for_bluesky(img_url)
            if not image_bytes:
                continue
            try:
                blob = client.upload_blob(image_bytes).blob
                image_blobs.append(blob)
                bsky_images.append(models.AppBskyEmbedImages.Image(alt=title, image=blob))
                print(f"Upload concluído: {img_url}")
            except Exception as e:
                print(f"Erro ao enviar imagem ao Bluesky: {e}")

        embed1 = models.AppBskyEmbedImages.Main(images=bsky_images) if bsky_images else None

        post1 = client.send_post(text=tb1, embed=embed1)
        print(f"Post 1 enviado para o Bluesky com {len(bsky_images)} imagem(ns).")

        tb2 = client_utils.TextBuilder()
        tb2.text("Se inscreva e leia na SUA 🫵 caixa de entrada!\n\n")
        tb2.link(url, url)

        card_thumb = image_blobs[0] if image_blobs else None

        embed2 = models.AppBskyEmbedExternal.Main(
            external=models.AppBskyEmbedExternal.External(
                title=title,
                description=short_desc,
                uri=url,
                thumb=card_thumb
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
    except Exception:
        return None

    if response.status_code != 200 or 'id' not in data:
        print(f"Erro ao criar container do Threads: {data}")
        return None

    return data['id']


def publicar_container_threads(container_id):
    publish_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
    payload = {'creation_id': container_id, 'access_token': THREADS_TOKEN}
    response = requests.post(publish_url, data=payload, timeout=REQUEST_TIMEOUT)
    
    try:
        data = response.json()
    except Exception:
        return None

    if response.status_code == 200 and 'id' in data:
        return data['id']

    print(f"Erro ao publicar container do Threads: {data}")
    return None


def criar_primeiro_post_threads_com_imagens(short_desc, images_to_post):
    images = images_to_post[:2]

    if len(images) >= 2:
        print("Threads: criando carrossel com 2 imagens.")
        child_container_ids = []

        for img_url in images:
            child_payload = {
                'media_type': 'IMAGE',
                'image_url': img_url,
                'is_carousel_item': 'true',
                'access_token': THREADS_TOKEN
            }
            child_id = criar_container_threads(child_payload)
            if not child_id:
                return None
            child_container_ids.append(child_id)
            print(f"Threads: container de carrossel criado para imagem: {img_url}")

        print(f"Threads: aguardando {THREADS_PROCESSING_WAIT} segundos para os itens do carrossel serem processados...")
        time.sleep(THREADS_PROCESSING_WAIT)

        carousel_payload = {
            'media_type': 'CAROUSEL',
            'children': ','.join(child_container_ids),
            'text': short_desc,
            'access_token': THREADS_TOKEN
        }
        carousel_container_id = criar_container_threads(carousel_payload)

        if not carousel_container_id:
            return None

        print(f"Threads: carrossel montado. Aguardando {THREADS_PROCESSING_WAIT} segundos para publicação...")
        time.sleep(THREADS_PROCESSING_WAIT)

        return publicar_container_threads(carousel_container_id)

    if len(images) == 1:
        print("Threads: criando post com 1 imagem.")
        image_payload = {
            'media_type': 'IMAGE',
            'image_url': images[0],
            'text': short_desc,
            'access_token': THREADS_TOKEN
        }
        image_container_id = criar_container_threads(image_payload)

        if not image_container_id:
            return None

        print(f"Threads: post com imagem criado. Aguardando {THREADS_PROCESSING_WAIT} segundos...")
        time.sleep(THREADS_PROCESSING_WAIT)

        return publicar_container_threads(image_container_id)

    print("Threads: nenhuma imagem encontrada. Criando primeiro post apenas com texto.")
    text_payload = {
        'media_type': 'TEXT',
        'text': short_desc,
        'access_token': THREADS_TOKEN
    }
    text_container_id = criar_container_threads(text_payload)

    if not text_container_id:
        return None

    print(f"Threads: post de texto criado. Aguardando {THREADS_PROCESSING_WAIT} segundos...")
    time.sleep(THREADS_PROCESSING_WAIT)

    return publicar_container_threads(text_container_id)


def criar_segundo_post_threads_com_link_preview(url, reply_to_id):
    second_text = f"Se inscreva e leia na SUA 🫵 caixa de entrada!\n\n🔗 {url}"
    reply_payload = {
        'media_type': 'TEXT',
        'text': second_text,
        'reply_to_id': reply_to_id,
        'link_attachment': url,
        'access_token': THREADS_TOKEN
    }
    reply_container_id = criar_container_threads(reply_payload)

    if not reply_container_id:
        return None

    print(f"Threads: segundo post com link preview criado. Aguardando {THREADS_PROCESSING_WAIT} segundos...")
    time.sleep(THREADS_PROCESSING_WAIT)

    return publicar_container_threads(reply_container_id)


def post_to_threads(title, short_desc, url, images_to_post):
    print("\n--- Iniciando postagem no Threads ---")

    if not THREADS_USER_ID or not THREADS_TOKEN:
        print("Threads não configurado: THREADS_USER_ID ou THREADS_TOKEN ausente.")
        return False

    try:
        threads_first_text = f"{short_desc}\n\n🔗 {url}"
        first_post_id = criar_primeiro_post_threads_com_imagens(threads_first_text, images_to_post)

        if not first_post_id:
            print("Threads: primeiro post não foi publicado.")
            return False

        print(f"Threads: primeiro post publicado com sucesso. ID: {first_post_id}")

        second_post_id = criar_segundo_post_threads_com_link_preview(url, first_post_id)

        if not second_post_id:
            print("Threads: segundo post com link preview não foi publicado.")
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
        # ==================================================================================================
        # ALTERAR AQUI (PASSO 1): 
        # Para ligar o bot no modo real e fazer com que a trava de histórico volte a funcionar, 
        # APAGUE o '#' do início da linha abaixo para ativar o "return":
        # ==================================================================================================
        # return 

    # Processa os textos
    title = html.unescape(latest_entry.title)
    description = clean_html_and_unescape(latest_entry.get('summary', 'Sem descrição'))
    short_desc = description[:240] + "..." if len(description) > 240 else description

    # Coleta as imagens, extrai HTML da página e roda o deduplicador avançado
    all_images = extract_image_urls(latest_entry, url)
    images_to_post = all_images[:2]

    print(f"Imagens finais filtradas e únicas: {len(all_images)}")
    print(f"Imagens selecionadas para postagem: {len(images_to_post)}")

    for index, img_url in enumerate(images_to_post, start=1):
        print(f"Imagem selecionada {index}: {img_url}")

    # ==================================================================================================
    # ALTERAR AQUI (PASSO 2): 
    # Este é o "Freio de Mão" do Teste a Seco. 
    # Quando for postar de verdade, APAGUE completamente estas duas linhas abaixo (o print e o return):
    # ==================================================================================================
    print("\n🛑 TESTE A SECO CONCLUÍDO! Parando o bot antes de postar nas redes.")
    return 

    sucesso_bsky = False
    sucesso_threads = False

    # ================= BLUESKY =================
    if already_bsky:
        print("\nBluesky: post mais recente já publicado anteriormente.")
    elif not bsky_config_ok:
        print("\nBluesky pulado: credenciais ausentes.")
    else:
        sucesso_bsky = post_to_bluesky(title, short_desc, url, images_to_post)

        if sucesso_bsky:
            save_posted_url(POSTED_BSKY_FILE, url)
            print(f"Histórico do Bluesky atualizado.")
        else:
            print("Histórico do Bluesky NÃO foi atualizado.")

    # ================= THREADS =================
    if already_threads:
        print("\nThreads: post mais recente já publicado anteriormente.")
    elif not threads_config_ok:
        print("\nThreads pulado: credenciais ausentes.")
    else:
        threads_basico_ok = checar_threads_basico()
        checar_threads_token_avancado()

        if not threads_basico_ok:
            print("Threads pulado: checagem básica falhou.")
        else:
            sucesso_threads = post_to_threads(title, short_desc, url, images_to_post)

            if sucesso_threads:
                save_posted_url(POSTED_THREADS_FILE, url)
                print(f"Histórico do Threads atualizado.")
            else:
                print("Histórico do Threads NÃO foi atualizado.")

    # ================= RESUMO =================
    print("\nResumo da execução:")
    print(f"Bluesky publicado nesta execução: {sucesso_bsky}")
    print(f"Threads publicado nesta execução: {sucesso_threads}")


if __name__ == '__main__':
    main()

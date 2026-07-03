import os
import time
import requests
import feedparser
import re
import html
from datetime import datetime
from zoneinfo import ZoneInfo
from atproto import Client, client_utils, models

# ================= CONFIGURAÇÕES =================
RSS_URL = 'https://newsletter.judao.com.br/feed'

# Histórico antigo, mantido só para migração
LEGACY_POSTED_FILE = 'posted_urls.txt'

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

REQUEST_TIMEOUT = 20
# =================================================


def is_time_allowed():
    tz_brasilia = ZoneInfo("America/Sao_Paulo")
    hour = datetime.now(tz_brasilia).hour
    print(f"Hora atual em Brasília: {hour}h")
    return 10 <= hour < 22


def get_posted_urls(file_path):
    """Carrega URLs já publicadas em um arquivo específico."""
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


def migrate_legacy_history_to_bluesky():
    """
    Migra o histórico antigo posted_urls.txt para o histórico do Bluesky.

    Importante:
    - O histórico antigo é migrado apenas para o Bluesky.
    - O Threads fica separado, para permitir novas tentativas caso ele tenha falhado antes.
    """
    legacy_urls = get_posted_urls(LEGACY_POSTED_FILE)

    if not legacy_urls:
        return

    bsky_urls = get_posted_urls(POSTED_BSKY_FILE)
    urls_to_migrate = legacy_urls - bsky_urls

    if not urls_to_migrate:
        return

    with open(POSTED_BSKY_FILE, 'a', encoding='utf-8') as f:
        for url in sorted(urls_to_migrate):
            f.write(url + '\n')

    print(f"Migração concluída: {len(urls_to_migrate)} URLs antigas copiadas para {POSTED_BSKY_FILE}.")


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


def checar_threads_basico():
    """
    Checa se o token do Threads responde para a conta configurada.

    Essa checagem confirma acesso básico.
    Ela não garante, sozinha, que a permissão de publicação está liberada.
    """
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
            print(f"HTTP status: {response.status_code}")
            print(response.text)
            return False

        if response.status_code != 200:
            print("Falha na checagem básica do Threads:")
            print(data)
            return False

        returned_id = str(data.get("id"))
        expected_id = str(THREADS_USER_ID)

        if returned_id != expected_id:
            print("Atenção: THREADS_USER_ID não bate com o token.")
            print(f"THREADS_USER_ID configurado: {expected_id}")
            print(f"ID retornado pela API: {returned_id}")
            return False

        print(f"Threads básico OK: token responde para @{data.get('username')}.")
        return True

    except Exception as e:
        print(f"Erro ao checar Threads: {e}")
        return False


def checar_threads_token_avancado():
    """
    Checa metadados do token via debug_token.

    Para usar esta função de forma completa, crie estes Secrets no GitHub:
    - THREADS_APP_ID
    - THREADS_APP_SECRET
    """
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
            print(f"HTTP status: {response.status_code}")
            print(response.text)
            return None

        if response.status_code != 200:
            print("Falha no debug_token:")
            print(data)
            return None

        token_data = data.get("data", {})
        scopes = token_data.get("scopes", []) or []

        print("Resultado do debug_token:")
        print(f"Token válido: {token_data.get('is_valid')}")
        print(f"App ID: {token_data.get('app_id')}")
        print(f"User ID: {token_data.get('user_id')}")
        print(f"Expira em: {token_data.get('expires_at')}")
        print(f"Scopes: {scopes}")

        if not token_data.get("is_valid"):
            print("Threads: token inválido segundo debug_token.")
            return token_data

        if "threads_basic" not in scopes:
            print("Atenção: parece faltar a permissão threads_basic.")

        if "threads_content_publish" not in scopes:
            print("Atenção: parece faltar a permissão threads_content_publish.")

        return token_data

    except Exception as e:
        print(f"Erro ao executar debug_token: {e}")
        return None


def post_to_bluesky(title, short_desc, url, images_to_post):
    print("\n--- Iniciando postagem no Bluesky ---")

    if not BSKY_HANDLE or not BSKY_PASSWORD:
        print("Bluesky não configurado: BSKY_HANDLE ou BSKY_PASSWORD ausente.")
        return False

    try:
        client = Client()
        client.login(BSKY_HANDLE, BSKY_PASSWORD)

        # Post 1: descrição + link + imagens
        tb1 = client_utils.TextBuilder()
        tb1.text(f"{short_desc}\n\n")
        tb1.link(url, url)

        image_blobs = []
        bsky_images = []

        for img_url in images_to_post:
            try:
                img_req = requests.get(img_url, timeout=REQUEST_TIMEOUT)

                if img_req.status_code == 200:
                    blob = client.upload_blob(img_req.content).blob
                    image_blobs.append(blob)
                    bsky_images.append(
                        models.AppBskyEmbedImages.Image(
                            alt=title,
                            image=blob
                        )
                    )
                else:
                    print(f"Imagem ignorada no Bluesky. HTTP {img_req.status_code}: {img_url}")

            except Exception as e:
                print(f"Erro ao baixar imagem para o Bluesky: {e}")

        embed1 = models.AppBskyEmbedImages.Main(images=bsky_images) if bsky_images else None

        post1 = client.send_post(text=tb1, embed=embed1)
        print("Post 1 enviado para o Bluesky.")

        # Post 2: texto fixo + card
        tb2 = client_utils.TextBuilder()
        tb2.text("Se inscreva e leia na SUA 🫵 caixa de entrada!")

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
        print("Post 2 (Thread) enviado para o Bluesky.")
        return True

    except Exception as e:
        print(f"Erro ao postar no Bluesky: {e}")
        return False


def post_to_threads(title, short_desc, url):
    print("\n--- Iniciando postagem no Threads ---")

    if not THREADS_USER_ID or not THREADS_TOKEN:
        print("Threads não configurado: THREADS_USER_ID ou THREADS_TOKEN ausente.")
        return False

    try:
        post_text = f"{title}\n\n{short_desc}\n\n🔗 {url}"

        # Passo 1: criar o rascunho/container
        create_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"
        create_payload = {
            'media_type': 'TEXT',
            'text': post_text,
            'access_token': THREADS_TOKEN
        }

        create_response = requests.post(
            create_url,
            data=create_payload,
            timeout=REQUEST_TIMEOUT
        )

        try:
            create_data = create_response.json()
        except Exception:
            print("Erro ao interpretar resposta do rascunho do Threads.")
            print(f"HTTP status: {create_response.status_code}")
            print(create_response.text)
            return False

        if create_response.status_code != 200 or 'id' not in create_data:
            print(f"Erro no rascunho do Threads: {create_data}")
            return False

        container_id = create_data['id']

        print(f"Rascunho criado (ID: {container_id}). Aguardando 10 segundos para a Meta processar...")
        time.sleep(10)

        # Passo 2: publicar o rascunho/container
        publish_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
        publish_payload = {
            'creation_id': container_id,
            'access_token': THREADS_TOKEN
        }

        publish_response = requests.post(
            publish_url,
            data=publish_payload,
            timeout=REQUEST_TIMEOUT
        )

        try:
            publish_data = publish_response.json()
        except Exception:
            print("Erro ao interpretar resposta da publicação do Threads.")
            print(f"HTTP status: {publish_response.status_code}")
            print(publish_response.text)
            return False

        if publish_response.status_code == 200 and 'id' in publish_data:
            print("Post enviado com sucesso para o Threads!")
            return True

        print(f"Erro na publicação do Threads: {publish_data}")
        return False

    except Exception as e:
        print(f"Erro ao postar no Threads: {e}")
        return False


def main():
    migrate_legacy_history_to_bluesky()

    bsky_config_ok = bool(BSKY_HANDLE and BSKY_PASSWORD)
    threads_config_ok = bool(THREADS_USER_ID and THREADS_TOKEN)

    if not bsky_config_ok and not threads_config_ok:
        print("Erro: nenhuma rede está configurada. Verifique os Secrets do GitHub.")
        print("Para Bluesky: BSKY_HANDLE e BSKY_PASSWORD.")
        print("Para Threads: THREADS_USER_ID e THREADS_TOKEN.")
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

    # Processa os textos
    title = html.unescape(latest_entry.title)
    description = clean_html_and_unescape(latest_entry.get('summary', 'Sem descrição'))
    short_desc = description[:240] + "..." if len(description) > 240 else description

    # Coleta as imagens para o Bluesky
    all_images = extract_image_urls(latest_entry)
    images_to_post = all_images[:2]

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
            print(f"Histórico do Bluesky atualizado com sucesso: {POSTED_BSKY_FILE}")
        else:
            print("Histórico do Bluesky NÃO foi atualizado, para tentar novamente no próximo ciclo.")

    # ================= THREADS =================
    if already_threads:
        print("\nThreads: post mais recente já publicado anteriormente.")
    elif not threads_config_ok:
        print("\nThreads pulado: credenciais ausentes.")
    else:
        threads_basico_ok = checar_threads_basico()

        # Checagem avançada opcional. Não bloqueia sozinha a publicação,
        # mas ajuda a diagnosticar problemas de token/permissão no log.
        checar_threads_token_avancado()

        if not threads_basico_ok:
            print("Threads pulado: checagem básica falhou.")
        else:
            sucesso_threads = post_to_threads(title, short_desc, url)

            if sucesso_threads:
                save_posted_url(POSTED_THREADS_FILE, url)
                print(f"Histórico do Threads atualizado com sucesso: {POSTED_THREADS_FILE}")
            else:
                print("Histórico do Threads NÃO foi atualizado, para tentar novamente no próximo ciclo.")

    # ================= RESUMO =================
    print("\nResumo da execução:")
    print(f"Bluesky já estava publicado: {already_bsky}")
    print(f"Threads já estava publicado: {already_threads}")
    print(f"Bluesky publicado nesta execução: {sucesso_bsky}")
    print(f"Threads publicado nesta execução: {sucesso_threads}")


if __name__ == '__main__':
    main()

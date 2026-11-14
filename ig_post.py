#!/usr/bin/python3
"""Posta reels no Instagram (@brunocostaft) pela API oficial da Meta, direto de um arquivo local.

Uso:
  ig_post.py check  VIDEO.mp4                       -> só valida o arquivo (não envia nada)
  ig_post.py reel   VIDEO.mp4 --caption "texto"     -> simulação: mostra o que seria postado
  ig_post.py reel   VIDEO.mp4 --caption-file leg.txt --publish   -> PUBLICA de verdade
      opções: --thumb-ms 1500 (frame da capa, em ms)  --no-feed (só na aba Reels)
  ig_post.py carousel URLS.txt --caption-file leg.txt [--validate|--publish]  -> carrossel de imagens por URL
  ig_post.py refresh                                -> renova o token por mais 60 dias
  ig_post.py status                                 -> conta, validade do token e cota de posts

Credenciais em ~/.config/instagram/.env (IG_TOKEN, IG_USER_ID, IG_APP_ID, IG_APP_SECRET).
"""
import argparse, json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

API = "v23.0"
GRAPH = f"https://graph.facebook.com/{API}"
ENV_PATH = os.path.expanduser("~/.config/instagram/.env")


def load_env():
    if not os.path.exists(ENV_PATH):  # na nuvem (GitHub Actions) as credenciais vêm de variáveis de ambiente
        env = {k: os.environ[k] for k in ("IG_TOKEN", "IG_USER_ID", "IG_APP_ID", "IG_APP_SECRET") if os.environ.get(k)}
        if "IG_TOKEN" not in env or "IG_USER_ID" not in env:
            sys.exit("Sem credenciais: faltam IG_TOKEN e IG_USER_ID")
        return env
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


def save_env(env):
    with open(ENV_PATH, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in env.items()))
    os.chmod(ENV_PATH, 0o600)


def call(method, url, token=None, params=None, data=None, headers=None):
    headers = dict(headers or {})
    if token:
        headers.setdefault("Authorization", f"Bearer {token}")
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    body = data
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or str(err)
        except Exception:
            msg = f"HTTP {e.code}"
        sys.exit(f"ERRO da API: {msg}")


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def check_video(path):
    if not os.path.isfile(path):
        sys.exit(f"Arquivo não encontrado: {path}")
    info = probe(path)
    fmt = info["format"]
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur, size = float(fmt["duration"]), int(fmt["size"])
    problems, notes = [], []
    if not v:
        problems.append("sem trilha de vídeo")
    else:
        w, h = v["width"], v["height"]
        num, den = (v["r_frame_rate"].split("/") + ["1"])[:2]
        fps = float(num) / float(den or 1)
        notes.append(f"{w}x{h} {v['codec_name']} {fps:.2f}fps")
        if v["codec_name"] not in ("h264", "hevc"):
            problems.append(f"codec {v['codec_name']} (precisa H.264 ou HEVC)")
        if abs(w / h - 9 / 16) > 0.02:
            notes.append("AVISO: não é 9:16, vai aparecer cortado/com barras no Reels")
        if not 23 <= fps <= 60:
            problems.append(f"{fps:.1f} fps (precisa 23–60)")
        if w > 1920:
            problems.append(f"largura {w}px (máx 1920)")
    if a:
        notes.append(f"áudio {a['codec_name']}")
        if a["codec_name"] != "aac":
            problems.append(f"áudio {a['codec_name']} (precisa AAC)")
    else:
        notes.append("sem áudio")
    notes.append(f"{dur:.1f}s, {size/1e6:.1f} MB")
    if not 3 <= dur <= 15 * 60:
        problems.append(f"duração {dur:.1f}s (precisa 3s–15min)")
    if size > 300e6:
        problems.append(f"{size/1e6:.0f} MB (máx 300 MB)")
    return problems, notes, size


CACHE = os.path.expanduser("~/tools/instagram/cache")


def needs_convert(path):
    info = probe(path)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    return v.get("width", 0) > 1920 or v.get("codec_name") not in ("h264", "hevc") \
        or (a is not None and a["codec_name"] != "aac")


def convert(path):
    """Cópia 1080x1920 H.264 alta qualidade + AAC, faststart. Não altera o original."""
    os.makedirs(CACHE, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(CACHE, base + "_ig1080.mp4")
    if os.path.exists(out) and os.path.getmtime(out) > os.path.getmtime(path):
        return out
    print("Convertendo para 1080x1920 (o original não é alterado)...")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-stats", "-i", path,
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,"
                           "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high",
                    "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
                    "-movflags", "+faststart", out], check=True)
    return out


def cmd_check(a):
    problems, notes, _ = check_video(a.video)
    print("Arquivo:", a.video)
    print("  " + " | ".join(notes))
    if problems:
        fix = " (o comando reel converte sozinho)" if needs_convert(a.video) else ""
        print("PROBLEMAS" + fix + ":\n  - " + "\n  - ".join(problems))
        sys.exit(1)
    print("OK para Reels.")


def cmd_reel(a):
    env = load_env()
    caption = a.caption or ""
    if a.caption_file:
        caption = open(a.caption_file, encoding="utf-8").read().strip()
    if needs_convert(a.video):
        a.video = convert(a.video)
    problems, notes, size = check_video(a.video)
    print("Arquivo:", a.video)
    print("  " + " | ".join(notes))
    if problems:
        sys.exit("Não dá para postar:\n  - " + "\n  - ".join(problems))
    if len(caption) > 2200:
        sys.exit(f"Legenda com {len(caption)} caracteres (máx 2200)")
    if caption.count("#") > 30:
        sys.exit("Mais de 30 hashtags")
    print(f"Conta: IG {env['IG_USER_ID']} | feed: {'não' if a.no_feed else 'sim'}"
          + (f" | capa em {a.thumb_ms} ms" if a.thumb_ms is not None else ""))
    print("Legenda:\n" + "-" * 40 + "\n" + caption + "\n" + "-" * 40)
    if not a.publish:
        print("SIMULAÇÃO — nada foi enviado. Rode de novo com --publish para postar.")
        return

    tok, ig = env["IG_TOKEN"], env["IG_USER_ID"]
    params = {"media_type": "REELS", "upload_type": "resumable", "caption": caption,
              "share_to_feed": "false" if a.no_feed else "true"}
    if a.thumb_ms is not None:
        params["thumb_offset"] = str(a.thumb_ms)
    c = call("POST", f"{GRAPH}/{ig}/media", tok, data=params)
    cid = c["id"]
    upload_uri = c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{API}/{cid}"
    print(f"Container {cid} criado. Enviando {size/1e6:.1f} MB...")
    with open(a.video, "rb") as f:
        body = f.read()
    r = call("POST", upload_uri, data=body, headers={
        "Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)})
    if not r.get("success", True):
        sys.exit(f"Upload falhou: {r}")
    print("Upload ok. Aguardando a Meta processar o vídeo...")
    for _ in range(120):  # até ~10 min
        s = call("GET", f"{GRAPH}/{cid}", tok, params={"fields": "status_code,status"})
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            sys.exit(f"Processamento falhou: {s.get('status')}")
        time.sleep(5)
    else:
        sys.exit(f"Demorou demais para processar. Container {cid} (pode tentar publicar depois).")
    m = call("POST", f"{GRAPH}/{ig}/media_publish", tok, data={"creation_id": cid})
    link = call("GET", f"{GRAPH}/{m['id']}", tok, params={"fields": "permalink"}).get("permalink")
    print(f"PUBLICADO: {link or m['id']}")


def wait_container(cid, tok, tries=120, every=5):
    for _ in range(tries):
        s = call("GET", f"{GRAPH}/{cid}", tok, params={"fields": "status_code,status"})
        code = s.get("status_code")
        if code in (None, "FINISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            sys.exit(f"Container {cid} falhou: {s.get('status')}")
        time.sleep(every)
    sys.exit(f"Container {cid} demorou demais para processar.")


def cmd_carousel(a):
    """Carrossel de imagens por URL pública (a API exige JPEG 4:5 a 1.91:1)."""
    env = load_env()
    tok, ig = env["IG_TOKEN"], env["IG_USER_ID"]
    urls = [l.strip() for l in open(a.urls_file) if l.strip() and not l.startswith("#")]
    caption = open(a.caption_file, encoding="utf-8").read().strip() if a.caption_file else (a.caption or "")
    if not 2 <= len(urls) <= 20:
        sys.exit(f"Carrossel precisa de 2 a 20 itens (tem {len(urls)})")
    if len(caption) > 2200 or caption.count("#") > 30:
        sys.exit("Legenda passa de 2200 caracteres ou 30 hashtags")
    print(f"Carrossel com {len(urls)} imagens | conta IG {ig}")
    for u in urls:
        print("  ", u)
    print("Legenda:\n" + "-" * 40 + "\n" + caption + "\n" + "-" * 40)
    if not (a.publish or a.validate):
        print("SIMULAÇÃO — nada foi enviado. --validate testa na API sem publicar; --publish posta.")
        return
    kids = []
    for n, u in enumerate(urls, 1):
        c = call("POST", f"{GRAPH}/{ig}/media", tok, data={"image_url": u, "is_carousel_item": "true"})
        kids.append(c["id"])
        print(f"  item {n}/{len(urls)} ok ({c['id']})")
    for k in kids:
        wait_container(k, tok, tries=24)
    c = call("POST", f"{GRAPH}/{ig}/media", tok, data={
        "media_type": "CAROUSEL", "children": ",".join(kids), "caption": caption})
    wait_container(c["id"], tok, tries=24)
    if not a.publish:
        print(f"VALIDADO: a API aceitou as {len(urls)} imagens e a legenda (container {c['id']}, não publicado, expira em 24h).")
        return
    m = call("POST", f"{GRAPH}/{ig}/media_publish", tok, data={"creation_id": c["id"]})
    link = call("GET", f"{GRAPH}/{m['id']}", tok, params={"fields": "permalink"}).get("permalink")
    print(f"PUBLICADO: {link or m['id']}")
    return link or m["id"]


def cmd_refresh(a):
    env = load_env()
    r = call("GET", f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token", "client_id": env["IG_APP_ID"],
        "client_secret": env["IG_APP_SECRET"], "fb_exchange_token": env["IG_TOKEN"]})
    env["IG_TOKEN"] = r["access_token"]
    if r.get("expires_in"):
        env["IG_TOKEN_EXPIRES"] = str(int(time.time() + r["expires_in"]))
    save_env(env)
    print("Token renovado." + (f" Expira em {r['expires_in']/86400:.0f} dias." if r.get("expires_in") else ""))


def cmd_status(a):
    env = load_env()
    tok, ig = env["IG_TOKEN"], env["IG_USER_ID"]
    me = call("GET", f"{GRAPH}/{ig}", tok, params={"fields": "username,followers_count,media_count"})
    d = call("GET", f"{GRAPH}/debug_token", params={"input_token": tok},
             headers={"Authorization": f"Bearer {env['IG_APP_ID']}|{env['IG_APP_SECRET']}"})["data"]
    q = call("GET", f"{GRAPH}/{ig}/content_publishing_limit", tok,
             params={"fields": "quota_usage,config"}).get("data", [{}])[0]
    exp = d.get("expires_at") or 0
    print(f"@{me.get('username')} | {me.get('followers_count')} seguidores | {me.get('media_count')} posts")
    print("Token:", "válido" if d.get("is_valid") else "INVÁLIDO",
          "| expira", time.strftime("%d/%m/%Y", time.localtime(exp)) if exp else "nunca")
    print(f"Cota 24h: {q.get('quota_usage', '?')}/{q.get('config', {}).get('quota_total', 100)} posts")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("video"); c.set_defaults(fn=cmd_check)
    r = sub.add_parser("reel"); r.add_argument("video")
    r.add_argument("--caption"); r.add_argument("--caption-file")
    r.add_argument("--thumb-ms", type=int); r.add_argument("--no-feed", action="store_true")
    r.add_argument("--publish", action="store_true"); r.set_defaults(fn=cmd_reel)
    k = sub.add_parser("carousel"); k.add_argument("urls_file", help="arquivo com 1 URL pública JPEG por linha")
    k.add_argument("--caption"); k.add_argument("--caption-file")
    k.add_argument("--validate", action="store_true"); k.add_argument("--publish", action="store_true")
    k.set_defaults(fn=cmd_carousel)
    sub.add_parser("refresh").set_defaults(fn=cmd_refresh)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Monta docs/dados.json para o calendário: posts agendados (jobs/*) + publicados (API do Instagram).
Roda no GitHub Actions com IG_TOKEN e IG_USER_ID nos secrets. Sem token, gera só a parte dos agendados."""
import json, os, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
REPO = os.environ.get("GITHUB_REPOSITORY", "brunocostaft/ig-midia")
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
API = "https://graph.facebook.com/v23.0"
TOKEN, IG = os.environ.get("IG_TOKEN"), os.environ.get("IG_USER_ID")


def get(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return {"erro": json.load(e).get("error", {}).get("message", str(e.code))}
        except Exception:
            return {"erro": f"HTTP {e.code}"}


def agendados():
    itens = []
    for ag in sorted((RAIZ / "jobs").glob("*/agenda.json")):
        job = ag.parent
        a = json.loads(ag.read_text())
        urls = [l.strip() for l in (job / "urls.txt").read_text().splitlines() if l.strip()] if (job / "urls.txt").exists() else []
        pub = (job / "PUBLICADO").read_text().strip() if (job / "PUBLICADO").exists() else None
        quando = datetime.fromisoformat(a["quando"])
        status = "publicado" if pub else ("atrasado" if quando < datetime.now(timezone.utc) else "agendado")
        itens.append({
            "id": job.name, "origem": "agenda", "titulo": a.get("titulo", job.name), "tipo": a.get("tipo", "post"),
            "quando": a["quando"], "status": status, "link": pub,
            "legenda": (job / "legenda.txt").read_text(encoding="utf-8").strip() if (job / "legenda.txt").exists() else "",
            "midias": urls,
        })
    return itens


def publicados():
    if not (TOKEN and IG):
        return [], "sem credenciais"
    campos = "id,caption,media_type,media_product_type,timestamp,permalink,thumbnail_url,media_url,like_count,comments_count,children{media_url,thumbnail_url}"
    r = get(f"{API}/{IG}/media?fields={campos}&limit=60")
    if "erro" in r:
        return [], r["erro"]
    itens, aviso = [], None
    for m in r.get("data", []):
        tipo = {"REELS": "reel", "STORY": "story"}.get(m.get("media_product_type"), "carrossel" if m["media_type"] == "CAROUSEL_ALBUM" else "foto")
        filhos = [c.get("media_url") or c.get("thumbnail_url") for c in m.get("children", {}).get("data", [])]
        capa = m.get("thumbnail_url") or m.get("media_url")
        met = {"curtidas": m.get("like_count"), "comentarios": m.get("comments_count")}
        ins = {"erro": "pulado"} if aviso else get(f"{API}/{m['id']}/insights?metric=reach,saved,shares,views")
        if "erro" in ins:
            aviso = "Métricas de alcance/salvamentos indisponíveis (falta a permissão instagram_manage_insights no token)."
        else:
            for d in ins.get("data", []):
                met[{"reach": "alcance", "saved": "salvamentos", "shares": "compartilhamentos", "views": "views"}[d["name"]]] = d["values"][0]["value"]
        itens.append({
            "id": m["id"], "origem": "instagram", "titulo": next((l for l in (m.get("caption") or "").split("\n") if sum(c.isalpha() for c in l) >= 3), "")[:90],
            "tipo": tipo, "quando": m["timestamp"].replace("+0000", "+00:00"), "status": "publicado",
            "link": m.get("permalink"), "legenda": m.get("caption") or "",
            "midias": [x for x in filhos if x] or ([capa] if capa else []), "metricas": met,
        })
    return itens, aviso


def main():
    ag = agendados()
    pub, aviso = publicados()
    # um post agendado que já saiu aparece uma vez só: junta com o publicado pelo link
    por_link = {p["link"]: p for p in pub if p.get("link")}
    for a in ag:
        p = por_link.get(a["link"]) if a["link"] else None
        if p:
            a["metricas"] = p.get("metricas")
            pub.remove(p)
    dados = {"gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "aviso": aviso if aviso != "sem credenciais" else None, "posts": ag + pub}
    (RAIZ / "docs").mkdir(exist_ok=True)
    (RAIZ / "docs/dados.json").write_text(json.dumps(dados, ensure_ascii=False, indent=1))
    print(f"{len(ag)} agendados, {len(pub)} publicados", "| aviso:", aviso)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Publica os posts agendados em jobs/*/agenda.json que chegaram na hora.
Regras: publica de 15 min antes até 24 h depois do horário (o cron do GitHub atrasa horas); nunca publica duas vezes (marcador PUBLICADO).
Uso:  python3 scripts/postar_agendados.py            -> publica o que estiver na hora
      python3 scripts/postar_agendados.py --validate JOB   -> testa um job na API sem publicar
      python3 scripts/postar_agendados.py --publish JOB    -> publica um job agora (manual)"""
import json, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RAW = "https://raw.githubusercontent.com/brunocostaft/ig-midia/main"
ANTES, DEPOIS = timedelta(minutes=15), timedelta(hours=24)


def comando(j, modo):
    a = json.loads((j / "agenda.json").read_text())
    if a.get("tipo") == "reel":  # vídeo do repo (upload resumível); validate = simulação do ig_post.py
        cmd = [sys.executable, str(RAIZ / "ig_post.py"), "reel", str(RAIZ / a["midia_dir"] / a.get("video", "video.mp4")),
               "--caption-file", str(j / "legenda.txt"),
               "--url", f"{RAW}/{a['midia_dir']}/{a.get('video', 'video.mp4')}"]
        if a.get("thumb_ms") is not None:
            cmd += ["--thumb-ms", str(a["thumb_ms"])]
        return cmd + (["--publish"] if modo == "publish" else [])
    return [sys.executable, str(RAIZ / "ig_post.py"), "carousel", str(j / "urls.txt"),
            "--caption-file", str(j / "legenda.txt"), f"--{modo}"]


def rodar(job, modo):
    j = RAIZ / "jobs" / job
    r = subprocess.run(comando(j, modo), capture_output=True, text=True)
    print(r.stdout + r.stderr)
    link = next((l.split(" ", 1)[1] for l in r.stdout.splitlines() if l.startswith("PUBLICADO:")), None)
    if modo == "publish" and link:
        (j / "PUBLICADO").write_text(link + "\n")
    return r.returncode == 0, link


def main():
    if len(sys.argv) == 3 and sys.argv[1] in ("--validate", "--publish"):
        ok, _ = rodar(sys.argv[2], sys.argv[1][2:])
        sys.exit(0 if ok else 1)
    agora = datetime.now(timezone.utc)
    falhou = False
    for ag in sorted((RAIZ / "jobs").glob("*/agenda.json")):
        job = ag.parent
        if (job / "PUBLICADO").exists():
            continue
        quando = datetime.fromisoformat(json.loads(ag.read_text())["quando"])
        if quando - ANTES <= agora <= quando + DEPOIS:
            print(f"== publicando {job.name} (agendado {quando.isoformat()})")
            ok, link = rodar(job.name, "publish")
            falhou |= not (ok and link)
        elif agora > quando + DEPOIS:
            print(f"!! {job.name} passou da janela sem publicar (agendado {quando.isoformat()})")
    sys.exit(1 if falhou else 0)


if __name__ == "__main__":
    main()

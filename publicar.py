#!/usr/bin/env python3
"""Publica en Instagram el santo del día: imagen(es) del repositorio + texto de textos.json.

    python publicar.py --simular            # enseña qué publicaría hoy, sin publicar
    python publicar.py --dia 10-05 --simular
    python publicar.py                      # publica de verdad el día de hoy
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from scrape_textos import construir_pie

REPO = "santosaldia/santos-del-dia"
RAMA = "main"
API = "https://graph.instagram.com/v23.0"
EXTENSIONES = (".jpg", ".jpeg")  # Instagram solo admite JPEG por API


def api(metodo, ruta, token, **params):
    params["access_token"] = token
    r = requests.request(
        metodo,
        f"{API}/{ruta}",
        params=params if metodo == "GET" else None,
        data=params if metodo == "POST" else None,
        timeout=60,
    )
    try:
        datos = r.json()
    except ValueError:
        datos = {}
    if "error" in datos or not r.ok:
        err = datos.get("error", {})
        sys.exit(f"Error de Instagram en '{ruta}': {err.get('message', r.text[:200])} (código {err.get('code')})")
    return datos


def imagenes_del_dia(clave):
    mm, dd = clave.split("-")
    return sorted(p for p in Path(".").glob(f"{mm}_{dd}*") if p.suffix.lower() in EXTENSIONES)


def url_publica(ruta):
    return f"https://raw.githubusercontent.com/{REPO}/{RAMA}/{quote(ruta.name)}"


def esperar(token, contenedor):
    for _ in range(30):
        estado = api("GET", contenedor, token, fields="status_code").get("status_code")
        if estado == "FINISHED":
            return
        if estado in ("ERROR", "EXPIRED"):
            sys.exit(f"Instagram no ha podido procesar la imagen (estado {estado}).")
        time.sleep(4)
    sys.exit("Instagram tarda demasiado en procesar la imagen.")


def ya_publicado(token, pie):
    primera = pie.splitlines()[0]
    recientes = api("GET", "me/media", token, fields="caption", limit=10).get("data", [])
    return any((m.get("caption") or "").splitlines()[:1] == [primera] for m in recientes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dia", help="MM-DD; por defecto, hoy (hora de Madrid)")
    ap.add_argument("--simular", action="store_true", help="no publica, solo enseña lo que haría")
    ap.add_argument("--forzar", action="store_true", help="publica aunque parezca ya publicado")
    args = ap.parse_args()

    clave = args.dia or datetime.now(ZoneInfo("Europe/Madrid")).strftime("%m-%d")
    textos = json.loads(Path("textos.json").read_text("utf-8"))
    if clave not in textos:
        sys.exit(f"No hay texto para el día {clave} en textos.json.")
    imgs = imagenes_del_dia(clave)
    if not imgs:
        sys.exit(f"No hay ninguna imagen .jpg para el día {clave} en el repositorio.")
    if len(imgs) > 10:
        sys.exit(f"Hay {len(imgs)} imágenes para {clave}; Instagram admite como máximo 10.")

    pie = construir_pie(textos[clave])
    urls = [url_publica(p) for p in imgs]

    print(f"Día: {clave}  |  Imágenes: {len(urls)}  |  Caracteres del texto: {len(pie)}")
    for u in urls:
        print("  ", u)
    print("---- TEXTO ----")
    print(pie)
    print("---------------")

    if args.simular:
        for u in urls:
            r = requests.head(u, timeout=30, allow_redirects=True)
            print(f"Comprobación de imagen: {'OK' if r.ok else 'NO ACCESIBLE (' + str(r.status_code) + ')'}  {u}")
        print("Simulación terminada: no se ha publicado nada.")
        return

    token = os.environ.get("IG_TOKEN", "").strip()
    if not token:
        sys.exit("Falta el secreto IG_TOKEN.")
    if not args.forzar and ya_publicado(token, pie):
        print("Parece que este día ya está publicado. No hago nada (usa --forzar para publicar igualmente).")
        return

    if len(urls) == 1:
        cont = api("POST", "me/media", token, image_url=urls[0], caption=pie)["id"]
    else:
        hijos = [api("POST", "me/media", token, image_url=u, is_carousel_item="true")["id"] for u in urls]
        for h in hijos:
            esperar(token, h)
        cont = api("POST", "me/media", token, media_type="CAROUSEL", children=",".join(hijos), caption=pie)["id"]
    esperar(token, cont)
    publicado = api("POST", "me/media_publish", token, creation_id=cont)
    print("Publicado correctamente. ID:", publicado.get("id"))


if __name__ == "__main__":
    main()

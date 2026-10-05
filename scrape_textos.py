#!/usr/bin/env python3
"""Scrapea el 'Santo del día' de Vatican News para los 366 días del año
y guarda textos.json con esta forma:

    {"10-05": [{"nombre": "ss. Plácido y Mauro...", "texto": "..."}, ...], ...}

Uso:
    pip install requests beautifulsoup4
    python scrape_textos.py --probar 10-05   # prueba un solo día y muestra el pie de foto
    python scrape_textos.py                  # todos los días, empezando de cero
    python scrape_textos.py --reanudar       # continúa con el textos.json que ya exista
"""
import argparse
import json
import re
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.vaticannews.va/es/santos/{mm}/{dd}.html"
SALIDA = Path("textos.json")
MANUALES = Path("textos_manuales.json")  # textos escritos a mano para santos que la web deja sin texto
CRUZ = "✝️"
FUENTE = "Fuente: Vatican News"
LIMITE = 2000  # Instagram admite 2200 caracteres en el pie de foto; dejamos margen


def descargar(mm, dd):
    """Descarga la página del día. Devuelve el HTML o None si falla."""
    for intento in range(3):
        try:
            r = requests.get(
                URL.format(mm=mm, dd=dd),
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0 (santos-al-dia)"},
            )
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
        except requests.RequestException:
            pass
        time.sleep(2 * (intento + 1))
    return None


def parsear(html):
    """Extrae la lista de santos {nombre, texto} de la página de un día.

    Recorre el texto de la página en orden: cada <h2> después del título
    'Santo del día' abre un santo nuevo, y el texto que sigue (sin enlaces
    como 'Leer todo...') es su resumen. Se detiene en 'Otros eventos programados'.
    """
    soup = BeautifulSoup(html, "html.parser")
    h1 = next(
        (h for h in soup.find_all("h1") if "santo del día" in h.get_text().lower()),
        None,
    )
    if h1 is None:
        return []

    santos, h2_actual = [], None
    for s in h1.find_all_next(string=True):
        txt = " ".join(s.split())
        if not txt:
            continue
        if "Otros eventos programados" in txt:
            break
        padres = [p.name for p in s.parents]
        if "script" in padres or "style" in padres:
            continue
        h2 = s.find_parent("h2")
        if h2 is not None:
            if h2 is not h2_actual:
                h2_actual = h2
                santos.append({"nombre": h2.get_text(" ", strip=True), "partes": []})
            continue
        if not santos or "a" in padres:
            continue
        santos[-1]["partes"].append(txt)

    return [
        {"nombre": s["nombre"], "texto": " ".join(s["partes"]).strip()}
        for s in santos
    ]


def recortar(texto, n):
    """Recorta a n caracteres, mejor en un final de frase."""
    if len(texto) <= n:
        return texto
    corte = texto[:n]
    punto = corte.rfind(". ")
    if punto > n // 2:
        return corte[: punto + 1]
    return corte[: corte.rfind(" ")].rstrip(",;:") + "…"


def construir_pie(santos, limite=LIMITE):
    """Pie de foto: ✝️ Nombre ✝️ + salto de línea + texto, para cada santo."""
    cabeceras = [f"{CRUZ} {s['nombre']} {CRUZ}" for s in santos]
    extra = f"\n\n{FUENTE}"
    fijo = sum(len(c) + 1 for c in cabeceras) + 2 * (len(santos) - 1) + len(extra)
    presupuesto = max((limite - fijo) // max(len(santos), 1), 100)
    bloques = [
        cab + (f"\n{recortar(s['texto'], presupuesto)}" if s["texto"] else "")
        for cab, s in zip(cabeceras, santos)
    ]
    return "\n\n".join(bloques) + extra


def normalizar(texto):
    """Minúsculas, sin tildes ni signos: para comparar nombres de santos."""
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def aplicar_manuales(datos, manuales):
    """Aplica textos_manuales.json: si el santo ya está ese día (mismo nombre,
    sin fijarse en tildes ni mayúsculas) se le cambia el texto; si no, se añade."""
    for clave, nuevos in manuales.items():
        actuales = datos.setdefault(clave, [])
        for nuevo in nuevos:
            for santo in actuales:
                if normalizar(santo["nombre"]) == normalizar(nuevo["nombre"]):
                    santo.update(nuevo)
                    break
            else:
                actuales.append(nuevo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probar", metavar="MM-DD", help="prueba un solo día, p. ej. 10-05")
    ap.add_argument(
        "--reanudar",
        action="store_true",
        help="continúa con el textos.json existente en vez de empezar de cero",
    )
    args = ap.parse_args()

    if args.probar:
        mm, dd = args.probar.split("-")
        html = descargar(mm, dd)
        if html is None:
            raise SystemExit("No se pudo descargar la página.")
        santos = parsear(html)
        print(json.dumps(santos, ensure_ascii=False, indent=2))
        print("-" * 40)
        print(construir_pie(santos))
        return

    if args.reanudar and SALIDA.exists():
        datos = json.loads(SALIDA.read_text("utf-8"))
    else:
        datos = {}
    fallos = []
    dia = date(2024, 1, 1)  # 2024 es bisiesto: así salen los 366 días
    while dia.year == 2024:
        clave = dia.strftime("%m-%d")
        if clave not in datos:
            html = descargar(*clave.split("-"))
            santos = parsear(html) if html else []
            if santos:
                datos[clave] = santos
                SALIDA.write_text(json.dumps(datos, ensure_ascii=False, indent=1), "utf-8")
                print(f"{clave}: {len(santos)} santo(s)")
            else:
                fallos.append(clave)
                print(f"{clave}: SIN DATOS")
            time.sleep(1)  # un segundo entre peticiones, por educación con la web
        dia += timedelta(days=1)

    # Textos escritos a mano para santos que la web deja sin texto
    if MANUALES.exists():
        manuales = json.loads(MANUALES.read_text("utf-8"))
        aplicar_manuales(datos, manuales)
        print(f"Aplicados textos manuales de: {', '.join(manuales)}")
    SALIDA.write_text(json.dumps(datos, ensure_ascii=False, indent=1), "utf-8")

    fallos = [d for d in fallos if d not in datos]
    sin_texto = [
        f"{clave} ({s['nombre']})"
        for clave, lista in datos.items()
        for s in lista
        if not s["texto"]
    ]
    print(f"\nListo: {len(datos)} días guardados en {SALIDA}.")
    if fallos:
        print("Días a revisar:", ", ".join(fallos))
    if sin_texto:
        print("Santos sin texto:", "; ".join(sin_texto))
    else:
        print("Todos los santos tienen texto.")


if __name__ == "__main__":
    main()

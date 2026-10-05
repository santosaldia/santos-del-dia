#!/usr/bin/env python3
"""Crea (y si se pide, publica como Reel) el vídeo del santo del día.

El vídeo usa las mismas imágenes y textos que la publicación diaria:
imagen con un zoom suave, voz de IA que lee el texto, subtítulos y,
si existe el archivo musica.mp3 en el repositorio, música de fondo.

    python crear_video.py --dia 10-05             # solo crea video.mp4
    python crear_video.py --dia 10-05 --publicar  # lo crea y lo publica
    python crear_video.py --publicar              # hoy (hora de Madrid)
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from publicar import API, api, construir_texto, imagenes_del_dia
from scrape_textos import normalizar

# --- Ajustes fáciles de cambiar -------------------------------------------
VOZ = "es-ES-AlvaroNeural"   # otras: es-ES-ElviraNeural (mujer), es-MX-JorgeNeural
VELOCIDAD = "-4%"            # un poco más pausado que lo normal
VOLUMEN_MUSICA = 0.12        # 0 = sin música; 1 = mismo volumen que la voz
DESPEDIDA = "Síguenos en Santos al Día."
# ---------------------------------------------------------------------------

ANCHO, ALTO, FPS = 1080, 1920, 30
CAJA_IMG = (960, 820)        # espacio máximo para la imagen del santo
Y_IMG = 330                  # posición vertical de la imagen
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
FUENTES_TITULO = ["/usr/share/fonts/opentype/ebgaramond/EBGaramond12-Bold.otf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"]
FUENTES_TEXTO = ["/usr/share/fonts/opentype/ebgaramond/EBGaramond12-Regular.otf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"]
DORADO = (224, 190, 120)


def fuente(lista, tam):
    for f in lista:
        if Path(f).exists():
            return ImageFont.truetype(f, tam)
    return ImageFont.load_default(tam)


def nombre_familia(lista):
    for f in lista:
        if Path(f).exists():
            return "EB Garamond 12" if "Garamond" in f else "DejaVu Serif"
    return "Sans"


# ---------- Texto que se lee en voz alta ----------

def nombre_hablado(nombre):
    """'s. Ángela de Foligno, religiosa' -> 'Santa Ángela de Foligno, religiosa'."""
    n = nombre.strip()
    m = re.match(r"^(ss|s|st|b|bb)\.?\s+(\S+)", n, re.I)
    if not m:
        return n
    abrev, primero = m.group(1).lower(), m.group(2)
    resto = n[m.end(1):].lstrip(". ")
    p = primero.lower().rstrip(",")
    femenino = (p.endswith("a") and p not in MASCULINOS_EN_A) or p in FEMENINOS \
        or bool(re.search(r"\b(virgen|viuda|religiosa|fundadora|abadesa|reina|monja|madre|hermana|esposa)\b", n, re.I))
    if abrev in ("ss", "bb"):
        titulo = "Beatos" if abrev == "bb" else "Santos"
    elif abrev == "b":
        titulo = "Beata" if femenino else "Beato"
    elif femenino:
        titulo = "Santa"
    elif re.match(r"^(to|do)", primero, re.I):  # Santo Tomás, Santo Domingo, Santo Toribio
        titulo = "Santo"
    else:
        titulo = "San"
    return sin_abreviaturas(f"{titulo} {resto}")


MASCULINOS_EN_A = {"luca", "bonaventura", "andrea", "nicola", "josafa", "bautista", "elia"}
FEMENINOS = {"isabel", "inés", "ines", "beatriz", "raquel", "pilar", "carmen", "mercedes", "dolores",
             "matilde", "clotilde", "leonor", "edith", "ruth", "felicidad", "luz", "eduviges",
             "gertrudis", "bernadette", "irene", "inmaculada", "soledad", "elisabet", "agnes", "isabel,"}


def sin_abreviaturas(texto):
    """'discípulos de s. Benito' -> 'discípulos de san Benito' (para que la voz lo lea bien)."""
    texto = re.sub(r"\bss\.\s+(?=[A-ZÁÉÍÓÚÑ])", "santos ", texto)
    return re.sub(r"\bs\.\s+(?=[A-ZÁÉÍÓÚÑ])", "san ", texto)


def fecha_larga(clave):
    mm, dd = clave.split("-")
    return f"{int(dd)} de {MESES[int(mm) - 1]}"


# ---------- Voz de IA (gratis, voces de Microsoft Edge) ----------

async def _voz(texto, mp3):
    import edge_tts
    com = edge_tts.Communicate(texto, VOZ, rate=VELOCIDAD, boundary="WordBoundary")
    palabras = []
    with open(mp3, "wb") as f:
        async for trozo in com.stream():
            if trozo["type"] == "audio":
                f.write(trozo["data"])
            elif trozo["type"] == "WordBoundary":
                ini = trozo["offset"] / 1e7
                palabras.append((ini, ini + trozo["duration"] / 1e7, trozo["text"]))
    return palabras


def duracion(archivo):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(archivo)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def generar_voz(texto, mp3, silencioso=False):
    """Devuelve (duración, lista de palabras con sus tiempos)."""
    if silencioso:  # solo para pruebas sin internet: audio mudo con tiempos estimados
        palabras, t = [], 0.3
        for p in texto.split():
            d = 0.12 + 0.055 * len(p)
            palabras.append((t, t + d, p))
            t += d + 0.05
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                        "-t", f"{t + 0.3:.2f}", str(mp3)], check=True)
        return duracion(mp3), palabras
    for intento in range(3):
        try:
            palabras = asyncio.run(_voz(texto, mp3))
            return duracion(mp3), palabras
        except Exception as e:  # fallo de red puntual: reintentar
            print(f"La voz ha fallado ({e}); reintento...")
            time.sleep(5)
    sys.exit("No se ha podido generar la voz.")


# ---------- Subtítulos ----------

def tiempo_ass(t):
    h, r = divmod(max(t, 0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def crear_subtitulos(palabras, archivo, max_palabras=5, max_letras=24):
    grupos, actual = [], []
    for p in palabras:
        actual.append(p)
        texto = " ".join(x[2] for x in actual)
        if len(actual) >= max_palabras or len(texto) >= max_letras or re.search(r"[.,;:!?]$", p[2]):
            grupos.append(actual)
            actual = []
    if actual:
        grupos.append(actual)
    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ANCHO}
PlayResY: {ALTO}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{nombre_familia(FUENTES_TITULO)},70,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,330,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lineas = []
    for i, g in enumerate(grupos):
        ini = g[0][0]
        fin = grupos[i + 1][0][0] if i + 1 < len(grupos) else g[-1][1] + 0.4
        fin = min(fin, g[-1][1] + 0.6)
        texto = " ".join(x[2] for x in g).replace("{", "(").replace("}", ")")
        lineas.append(f"Dialogue: 0,{tiempo_ass(ini)},{tiempo_ass(fin)},Sub,,0,0,0,,{texto}")
    Path(archivo).write_text(cab + "\n".join(lineas) + "\n", "utf-8")


# ---------- Imágenes ----------

def emparejar(santos, imgs):
    """Asigna a cada santo la imagen cuyo nombre de archivo más se le parece."""
    def palabras(t):
        return {w for w in normalizar(t).split() if len(w) > 2 and not w.isdigit()}
    restantes = list(imgs)
    asignadas = []
    for i, s in enumerate(santos):
        ns = palabras(s["nombre"])
        mejor = max(imgs, key=lambda p: len(ns & palabras(p.stem)))
        if len(ns & palabras(mejor.stem)) == 0:
            # sin coincidencia: una imagen aún no usada, o la que toque por orden
            mejor = restantes[0] if restantes else imgs[i % len(imgs)]
        if mejor in restantes:
            restantes.remove(mejor)
        asignadas.append(mejor)
    return asignadas


def ajustar_texto(draw, texto, fnt, ancho):
    lineas, actual = [], ""
    for w in texto.split(" "):
        prueba = f"{actual} {w}".strip()
        if draw.textlength(prueba, font=fnt) <= ancho:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = w
    if actual:
        lineas.append(actual)
    return lineas


def fondo(img_path, titulo, fecha, salida):
    """Fondo fijo: la imagen difuminada, la fecha arriba y el nombre del santo."""
    img = Image.open(img_path).convert("RGB")
    esc = max(ANCHO / img.width, ALTO / img.height)
    bg = img.resize((int(img.width * esc) + 1, int(img.height * esc) + 1), Image.LANCZOS)
    bg = bg.crop(((bg.width - ANCHO) // 2, (bg.height - ALTO) // 2,
                  (bg.width - ANCHO) // 2 + ANCHO, (bg.height - ALTO) // 2 + ALTO))
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.38)
    d = ImageDraw.Draw(bg)

    f_marca, f_fecha = fuente(FUENTES_TEXTO, 44), fuente(FUENTES_TITULO, 70)
    d.text((ANCHO / 2, 150), "SANTOS AL DÍA", font=f_marca, fill=DORADO, anchor="mm")
    d.text((ANCHO / 2, 230), fecha.capitalize(), font=f_fecha, fill="white", anchor="mm")

    w, h = medida_imagen(img)
    y_nombre = Y_IMG + h + 70
    f_nombre = fuente(FUENTES_TITULO, 62)
    lineas = ajustar_texto(d, f"† {titulo}\u00a0†", f_nombre, 940)
    for i, linea in enumerate(lineas[:3]):
        d.text((ANCHO / 2, y_nombre + i * 76), linea, font=f_nombre, fill=DORADO, anchor="ma")
    # marco dorado alrededor de la imagen
    x0 = (ANCHO - w) // 2
    d.rectangle([x0 - 6, Y_IMG - 6, x0 + w + 5, Y_IMG + h + 5], outline=DORADO, width=3)
    bg.save(salida)


def medida_imagen(img):
    esc = min(CAJA_IMG[0] / img.width, CAJA_IMG[1] / img.height)
    w, h = int(img.width * esc), int(img.height * esc)
    return w - w % 2, h - h % 2


# ---------- Montaje ----------

def segmento(img_path, titulo, fecha, texto, carpeta, n, silencioso):
    mp3 = carpeta / f"voz{n}.mp3"
    dur_voz, palabras = generar_voz(texto, mp3, silencioso)
    dur = dur_voz + 0.6
    crear_subtitulos(palabras, carpeta / f"sub{n}.ass")
    fondo(img_path, titulo, fecha, carpeta / f"fondo{n}.png")

    w, h = medida_imagen(Image.open(img_path))
    x0 = (ANCHO - w) // 2
    frames = int(dur * FPS) + 1
    filtro = (
        f"[1:v]scale={w * 2}:{h * 2}:flags=lanczos,"
        f"zoompan=z='1+0.10*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={FPS},"
        f"unsharp=5:5:0.6[img];"
        f"[0:v][img]overlay={x0}:{Y_IMG}:shortest=1,"
        f"subtitles={carpeta / f'sub{n}.ass'},"
        f"fade=t=in:st=0:d=0.4,fade=t=out:st={dur - 0.4:.2f}:d=0.4,format=yuv420p[v]"
    )
    salida = carpeta / f"seg{n}.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.2f}", "-i", str(carpeta / f"fondo{n}.png"),
        "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.2f}", "-i", str(img_path),
        "-i", str(mp3),
        "-filter_complex", filtro + f";[2:a]apad=whole_dur={dur:.2f},aresample=44100[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "160k", "-ac", "2",
        str(salida),
    ], check=True)
    return salida


def crear_video(clave, textos, salida, silencioso=False):
    santos = textos[clave]
    imgs = imagenes_del_dia(clave)
    if not imgs:
        sys.exit(f"No hay imagen para el día {clave}.")
    fecha = fecha_larga(clave)
    asignadas = emparejar(santos, imgs)

    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        segs = []
        for n, (s, img) in enumerate(zip(santos, asignadas)):
            hablado = nombre_hablado(s["nombre"])
            texto = f"{hablado}. {sin_abreviaturas(s['texto'])}".strip()
            if n == 0:
                texto = f"Santo del día, {fecha}. {texto}"
            if n == len(santos) - 1:
                texto = f"{texto} {DESPEDIDA}"
            print(f"  Segmento {n + 1}/{len(santos)}: {hablado}  ({img.name})")
            segs.append(segmento(img, hablado, fecha, texto, carpeta, n, silencioso))

        lista = carpeta / "lista.txt"
        lista.write_text("".join(f"file '{p}'\n" for p in segs))
        unido = carpeta / "unido.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lista), "-c", "copy", str(unido)], check=True)

        musica = Path("musica.mp3")
        if musica.exists() and VOLUMEN_MUSICA > 0:
            total = duracion(unido)
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-i", str(unido), "-stream_loop", "-1", "-i", str(musica),
                "-filter_complex",
                f"[1:a]volume={VOLUMEN_MUSICA},afade=t=out:st={max(total - 2, 0):.2f}:d=2[m];"
                f"[0:a][m]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(salida),
            ], check=True)
        else:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(unido), "-c", "copy",
                            "-movflags", "+faststart", str(salida)], check=True)
    print(f"Vídeo creado: {salida} ({duracion(salida):.0f} segundos)")


# ---------- Publicar en Instagram como Reel ----------

def reel_ya_publicado(token, pie):
    primera = pie.splitlines()[0]
    recientes = api("GET", "me/media", token, fields="caption,media_type", limit=10).get("data", [])
    return any(m.get("media_type") == "VIDEO" and (m.get("caption") or "").splitlines()[:1] == [primera]
               for m in recientes)


def publicar_reel(video, pie, token):
    version = API.rsplit("/", 1)[-1]
    cont = api("POST", "me/media", token, media_type="REELS", upload_type="resumable",
               caption=pie, share_to_feed="true")["id"]
    datos = Path(video).read_bytes()
    r = requests.post(
        f"https://rupload.facebook.com/ig-api-upload/{version}/{cont}",
        headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(datos))},
        data=datos, timeout=300,
    )
    if not r.ok:
        sys.exit(f"Error al subir el vídeo a Instagram: {r.text[:300]}")
    print("Vídeo subido. Esperando a que Instagram lo procese...")
    for _ in range(60):  # hasta 10 minutos
        estado = api("GET", cont, token, fields="status_code").get("status_code")
        if estado == "FINISHED":
            break
        if estado in ("ERROR", "EXPIRED"):
            sys.exit(f"Instagram no ha podido procesar el vídeo (estado {estado}).")
        time.sleep(10)
    else:
        sys.exit("Instagram tarda demasiado en procesar el vídeo.")
    publicado = api("POST", "me/media_publish", token, creation_id=cont)
    print("Reel publicado correctamente. ID:", publicado.get("id"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dia", help="MM-DD; por defecto, hoy (hora de Madrid)")
    ap.add_argument("--publicar", action="store_true", help="publica el vídeo en Instagram")
    ap.add_argument("--forzar", action="store_true", help="publica aunque parezca ya publicado")
    ap.add_argument("--sin-voz", action="store_true", help="(pruebas) vídeo mudo, sin conectar a la voz")
    ap.add_argument("--salida", default="video.mp4")
    args = ap.parse_args()

    clave = args.dia or datetime.now(ZoneInfo("Europe/Madrid")).strftime("%m-%d")
    textos = json.loads(Path("textos.json").read_text("utf-8"))
    if clave not in textos:
        sys.exit(f"No hay texto para el día {clave} en textos.json.")
    pie = construir_texto(textos[clave])

    token = ""
    if args.publicar:
        token = os.environ.get("IG_TOKEN", "").strip()
        if not token:
            sys.exit("Falta el secreto IG_TOKEN.")
        if not args.forzar and reel_ya_publicado(token, pie):
            print("El Reel de hoy ya está publicado. No hago nada.")
            return

    print(f"Creando el vídeo del {fecha_larga(clave)}...")
    crear_video(clave, textos, args.salida, silencioso=args.sin_voz)
    if args.publicar:
        publicar_reel(args.salida, pie, token)
    else:
        print("No se ha publicado (modo prueba).")


if __name__ == "__main__":
    main()

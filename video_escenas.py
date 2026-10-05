#!/usr/bin/env python3
"""Vídeo del santo del día con escenas dibujadas por IA (prueba).

Lee escenas/MM-DD.json (texto narrado + descripción de cada escena),
pide a Pollinations (gratis) una ilustración por escena, y monta el vídeo
con movimiento de cámara, transiciones, voz de IA y subtítulos.

    python video_escenas.py --dia 10-05
"""
import argparse
import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image

from crear_video import (ANCHO, ALTO, FPS, FUENTES_TITULO, fecha_larga, generar_voz,
                         nombre_familia, tiempo_ass)

ESTILO = ("classical religious oil painting in the style of Caravaggio and Murillo, "
          "dramatic chiaroscuro lighting, warm golden tones, highly detailed, "
          "cinematic vertical composition, no text, no letters, no watermark")
TRANSICION = 0.6  # segundos de fundido entre escenas
PAUSA = 0.35      # silencio después de cada frase


def dibujar(prompt, destino, semilla):
    """Pide la imagen a Pollinations; reintenta si falla."""
    texto = f"{prompt}, {ESTILO}"
    urls = [
        f"https://image.pollinations.ai/prompt/{quote(texto)}"
        f"?width=1080&height=1920&model=flux&nologo=true&enhance=false&seed={semilla}",
        f"https://gen.pollinations.ai/image/{quote(texto)}"
        f"?width=1080&height=1920&model=flux&nologo=true&seed={semilla}",
    ]
    for intento in range(6):
        url = urls[intento % len(urls)]
        try:
            r = requests.get(url, timeout=240)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                destino.write_bytes(r.content)
                img = Image.open(destino).convert("RGB")
                print(f"    imagen OK {img.size} ({url.split('/')[2]})")
                img.save(destino, quality=95)
                return
            print(f"    intento {intento + 1}: {r.status_code} {r.text[:150]!r}")
        except Exception as e:
            print(f"    intento {intento + 1}: {e}")
        time.sleep(15)
    sys.exit("Pollinations no ha devuelto la imagen.")


def clip(img, dur, n, salida):
    """Imagen con movimiento de cámara (acercar, alejar o desplazar)."""
    frames = int(dur * FPS) + 1
    mov = n % 3
    if mov == 0:    # acercar
        z, x, y = f"1+0.12*on/{frames}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif mov == 1:  # alejar
        z, x, y = f"1.12-0.12*on/{frames}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    else:           # subir despacio
        z, x, y = "1.12", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(1-on/{frames})"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}",
        "-i", str(img), "-filter_complex",
        f"scale={ANCHO * 2}:{ALTO * 2}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={ANCHO * 2}:{ALTO * 2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={ANCHO}x{ALTO}:fps={FPS},format=yuv420p",
        "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(salida),
    ], check=True)


def subtitulos(escenas, inicios, palabras_por_escena, fecha, archivo):
    fuente = nombre_familia(FUENTES_TITULO)
    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ANCHO}
PlayResY: {ALTO}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{fuente},68,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,5,3,2,70,70,420,1
Style: Marca,{fuente},46,&H0078BEE0,&H0078BEE0,&H00000000,&H96000000,0,0,0,0,100,100,6,0,1,3,2,8,60,60,170,1
Style: Fecha,{fuente},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,5,3,8,60,60,240,1
Style: Rotulo,{fuente},70,&H0078BEE0,&H0078BEE0,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,5,3,8,70,70,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    fin_intro = inicios[1] if len(inicios) > 1 else 4
    ev.append(f"Dialogue: 1,{tiempo_ass(0.2)},{tiempo_ass(fin_intro)},Marca,,0,0,0,,"
              r"{\fad(500,400)}SANTOS AL DÍA")
    ev.append(f"Dialogue: 1,{tiempo_ass(0.4)},{tiempo_ass(fin_intro)},Fecha,,0,0,0,,"
              + r"{\fad(500,400)}" + fecha.capitalize())
    for i, esc in enumerate(escenas):
        if esc.get("rotulo"):
            ev.append(f"Dialogue: 1,{tiempo_ass(inicios[i] + 0.3)},{tiempo_ass(inicios[i] + 4.5)},Rotulo,,0,0,0,,"
                      + r"{\fad(400,400)}† " + esc["rotulo"] + " †")
        # subtítulos de 3-5 palabras sincronizados con la voz
        palabras = [(a + inicios[i], b + inicios[i], t) for a, b, t in palabras_por_escena[i]]
        grupo = []
        for j, p in enumerate(palabras):
            grupo.append(p)
            texto = " ".join(x[2] for x in grupo)
            ultimo = j == len(palabras) - 1
            if ultimo or len(grupo) >= 5 or len(texto) >= 22 or texto[-1] in ".,;:!?":
                fin = palabras[j + 1][0] if not ultimo else grupo[-1][1] + 0.3
                ev.append(f"Dialogue: 0,{tiempo_ass(grupo[0][0])},{tiempo_ass(min(fin, grupo[-1][1] + 0.5))},"
                          f"Sub,,0,0,0,,{texto}")
                grupo = []
    Path(archivo).write_text(cab + "\n".join(ev) + "\n", "utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dia", required=True, help="MM-DD")
    ap.add_argument("--salida", default="video.mp4")
    ap.add_argument("--sin-voz", action="store_true", help="(pruebas) sin conectar a la voz")
    args = ap.parse_args()

    datos = json.loads(Path(f"escenas/{args.dia}.json").read_text("utf-8"))
    escenas = datos["escenas"]
    fecha = fecha_larga(args.dia)
    semilla = random.randint(1, 10**6)

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        audios, durs, palabras = [], [], []
        for n, esc in enumerate(escenas):
            print(f"Escena {n + 1}/{len(escenas)}: {esc['texto'][:60]}")
            dibujar(esc["prompt"], t / f"img{n}.jpg", semilla + n)
            mp3 = t / f"voz{n}.mp3"
            d, pal = generar_voz(esc["texto"], mp3, args.sin_voz)
            # empezamos a hablar 0.3 s después de que entre la imagen
            pal = [(a + 0.3, b + 0.3, w) for a, b, w in pal]
            wav = t / f"voz{n}.wav"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-af",
                            f"adelay=300:all=1,apad=pad_dur={PAUSA}", "-ar", "44100", "-ac", "2", str(wav)],
                           check=True)
            audios.append(wav)
            durs.append(d + 0.3 + PAUSA)
            palabras.append(pal)

        # cada imagen dura lo que su frase, más el fundido con la siguiente
        inicios = [sum(durs[:i]) for i in range(len(durs))]
        clips = []
        for n in range(len(escenas)):
            c = t / f"clip{n}.mp4"
            clip(t / f"img{n}.jpg", durs[n] + (TRANSICION if n < len(escenas) - 1 else 0.8), n, c)
            clips.append(c)

        entradas, filtro, prev = [], [], "[0:v]"
        for c in clips:
            entradas += ["-i", str(c)]
        for n in range(1, len(clips)):
            sal = f"[x{n}]"
            filtro.append(f"{prev}[{n}:v]xfade=transition=fade:duration={TRANSICION}:"
                          f"offset={inicios[n]:.3f}{sal}")
            prev = sal
        total = sum(durs) + 0.8
        subtitulos(escenas, inicios, palabras, fecha, t / "subs.ass")
        filtro.append(f"{prev}subtitles={t / 'subs.ass'},fade=t=in:st=0:d=0.5,"
                      f"fade=t=out:st={total - 0.8:.2f}:d=0.8[v]")
        k = len(clips)
        for a in audios:
            entradas += ["-i", str(a)]
        filtro.append("".join(f"[{k + i}:a]" for i in range(len(audios)))
                      + f"concat=n={len(audios)}:v=0:a=1,apad=whole_dur={total:.2f}[a]")
        subprocess.run(["ffmpeg", "-y", "-v", "error", *entradas, "-filter_complex", ";".join(filtro),
                        "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS),
                        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", args.salida], check=True)
        # guardamos también las imágenes para revisarlas
        Path("escenas_img").mkdir(exist_ok=True)
        for n in range(len(escenas)):
            (Path("escenas_img") / f"{args.dia}_{n + 1}.jpg").write_bytes((t / f"img{n}.jpg").read_bytes())
    print(f"Vídeo creado: {args.salida} ({total:.0f} segundos)")


if __name__ == "__main__":
    main()

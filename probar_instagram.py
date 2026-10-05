"""Prueba de conexión con Instagram: muestra el nombre de la cuenta, sin publicar nada."""
import os
import sys

import requests

VERSION = "v23.0"

token = os.environ.get("IG_TOKEN", "").strip()
if not token:
    sys.exit("Falta el secreto IG_TOKEN en GitHub (Settings > Secrets and variables > Actions).")

try:
    r = requests.get(
        f"https://graph.instagram.com/{VERSION}/me",
        params={"fields": "user_id,username,account_type,media_count", "access_token": token},
        timeout=30,
    )
    datos = r.json()
except Exception as e:
    sys.exit(f"No se pudo conectar con Instagram ({type(e).__name__}).")

if "error" in datos:
    err = datos["error"]
    sys.exit(f"Instagram ha respondido con un error: {err.get('message')} (código {err.get('code')})")

print("Conexión correcta")
print("  Cuenta:", datos.get("username"))
print("  Tipo:", datos.get("account_type"))
print("  ID:", datos.get("user_id") or datos.get("id"))
print("  Publicaciones:", datos.get("media_count"))

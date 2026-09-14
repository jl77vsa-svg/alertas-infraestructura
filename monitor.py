#!/usr/bin/env python3
"""
Sistema de monitoreo de palabras clave -> alertas a Telegram.

Fuentes (todas gratuitas, sin necesidad de API keys de pago):
  - Google News RSS (noticias de todo el mundo, en español o el idioma que definas)
  - GDELT Project (base de datos global de noticias, actualizada cada 15 min)
  - Reddit (búsqueda pública, sin autenticación)

Cómo funciona:
  1. Lee las palabras clave desde keywords.txt
  2. Consulta cada fuente por cada palabra clave
  3. Compara contra seen_state.json para no repetir alertas ya enviadas
  4. Envía las coincidencias nuevas a un chat de Telegram vía Bot API
  5. Guarda el estado actualizado (seen_state.json) para la próxima corrida

Diseñado para correr periódicamente sin estado en memoria (cada ejecución es
independiente), por eso el "seen_state.json" se persiste en el repo entre
corridas (ver el workflow de GitHub Actions).
"""

import json
import os
import re
import sys
import time
import hashlib
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import xml.etree.ElementTree as ET
import requests

STATE_FILE = "seen_state.json"
KEYWORDS_FILE = "keywords.txt"
MAX_AGE_DAYS = 7          # cuánto tiempo se conserva un ID en el estado antes de olvidarlo
REQUEST_TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (compatible; KeywordMonitorBot/1.0; +https://github.com/)"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def normalize(text):
    """Minúsculas y sin tildes, para comparar coincidencias de forma más flexible."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text


def load_keywords():
    if not os.path.exists(KEYWORDS_FILE):
        log(f"ERROR: no existe {KEYWORDS_FILE}")
        return []
    with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
        kws = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    return kws


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"WARNING: no se pudo leer {STATE_FILE} ({e}), empezando de cero")
    return {}


def save_state(state):
    # Poda entradas viejas para que el archivo no crezca indefinidamente
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    pruned = {k: v for k, v in state.items() if v.get("ts", 0) >= cutoff}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=2)


def item_id(url, title):
    h = hashlib.sha256((url + "|" + title).encode("utf-8")).hexdigest()
    return h[:24]


def matches_keyword(text, keyword):
    return normalize(keyword) in normalize(text)


# ---------------------------------------------------------------------------
# Fuentes
# ---------------------------------------------------------------------------

def search_google_news(keyword, lang="es-419", country="EC"):
    """Google News RSS - gratuito, sin API key, cubre miles de medios."""
    results = []
    url = (
        f"https://news.google.com/rss/search?q={quote_plus(keyword)}"
        f"&hl={lang}&gl={country}&ceid={country}:{lang.split('-')[0]}"
    )
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            log(f"WARNING Google News status {r.status_code} para '{keyword}'")
            return results
        root = ET.fromstring(r.content)
        for item in root.findall("./channel/item")[:15]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            source_el = item.find("source")
            source = source_el.text.strip() if source_el is not None and source_el.text else "Google News"
            published = (item.findtext("pubDate") or "").strip()
            results.append({
                "source": f"Noticias ({source})",
                "title": title,
                "url": link,
                "published": published,
            })
    except ET.ParseError as e:
        log(f"ERROR parseando XML de Google News para '{keyword}': {e}")
    except Exception as e:
        log(f"ERROR Google News para '{keyword}': {e}")
    return results


def search_gdelt(keyword):
    """GDELT Project - monitoreo global de noticias, actualizado ~cada 15 min, gratuito."""
    results = []
    # Frase entre comillas = búsqueda exacta; timespan acota a lo reciente y evita
    # respuestas vacías/HTML que rompen el parseo JSON.
    query = f'"{keyword}"' if " " in keyword else keyword
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc?"
        f"query={quote_plus(query)}&mode=artlist&maxrecords=15&format=json"
        "&sort=datedesc&timespan=3d"
    )
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        if r.status_code != 200 or not r.text.strip():
            log(f"WARNING GDELT status {r.status_code} (vacío) para '{keyword}'")
            return results
        try:
            data = r.json()
        except ValueError:
            # GDELT a veces responde HTML/texto de error en vez de JSON (rate-limit,
            # mantenimiento, etc.). No es un fallo del script, se ignora esta corrida.
            log(f"WARNING GDELT devolvió una respuesta no-JSON para '{keyword}' "

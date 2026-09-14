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
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import xml.etree.ElementTree as ET
import requests

STATE_FILE = "seen_state.json"
KEYWORDS_FILE = "keywords.txt"
MAX_AGE_DAYS = 7          # cuánto tiempo se conserva un ID en el estado antes de olvidarlo
MAX_NEWS_AGE_HOURS = 24   # solo se alerta sobre lo publicado en las últimas 24 horas
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


def parse_rfc822_date(date_str):
    """Parsea el formato de fecha de Google News RSS (RFC 822), p.ej.
    'Mon, 14 Sep 2026 13:59:43 GMT'. Devuelve None si no se puede parsear."""
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_gdelt_date(date_str):
    """Parsea el formato de fecha de GDELT, p.ej. '20260914T135943Z'."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y%m%dT%H%M%SZ")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def is_recent(item, max_age_hours=MAX_NEWS_AGE_HOURS):
    """True si el ítem se publicó dentro de las últimas `max_age_hours` horas.
    Si no se pudo determinar la fecha de publicación, se descarta por seguridad
    (evita mandar alertas de noticias viejas cuando la fecha viene vacía)."""
    dt = item.get("published_dt")
    if dt is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    return dt >= cutoff


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
                "published_dt": parse_rfc822_date(published),
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
        "&sort=datedesc&timespan=1d"
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
                f"(primeros 120 caracteres: {r.text[:120]!r})")
            return results
        for art in data.get("articles", []):
            published = art.get("seendate", "")
            results.append({
                "source": f"GDELT ({art.get('domain', 'web')})",
                "title": art.get("title", ""),
                "url": art.get("url", ""),
                "published": published,
                "published_dt": parse_gdelt_date(published),
            })
    except Exception as e:
        log(f"ERROR GDELT para '{keyword}': {e}")
    return results


def search_reddit(keyword):
    """Búsqueda pública de Reddit (endpoint JSON sin autenticación).

    Reddit bloquea con frecuencia las IPs de servidores en la nube (incluidas
    las de GitHub Actions) con un 403, independientemente de las cabeceras.
    Cuando eso pasa no hay mucho que hacer del lado del script: se registra
    como advertencia y se sigue con las demás fuentes.
    """
    results = []
    url = f"https://www.reddit.com/search.json?q={quote_plus(keyword)}&sort=new&limit=15"
    headers = {
        "User-Agent": "web:keyword-monitor-bot:v1.0 (by /u/keyword_monitor)",
        "Accept": "application/json",
    }
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        if r.status_code == 403:
            log(f"WARNING Reddit bloqueó la solicitud (403) para '{keyword}' "
                f"— esto es un bloqueo de Reddit a IPs de servidores en la nube, "
                f"no un error de configuración.")
            return results
        if r.status_code != 200:
            log(f"WARNING Reddit status {r.status_code} para '{keyword}'")
            return results
        data = r.json()
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            title = d.get("title", "")
            permalink = d.get("permalink", "")
            subreddit = d.get("subreddit", "")
            published_dt = datetime.fromtimestamp(
                d.get("created_utc", time.time()), tz=timezone.utc
            ) if d.get("created_utc") is not None else None
            results.append({
                "source": f"Reddit (r/{subreddit})",
                "title": title,
                "url": f"https://www.reddit.com{permalink}" if permalink else d.get("url", ""),
                "published": published_dt.isoformat() if published_dt else "",
                "published_dt": published_dt,
            })
    except Exception as e:
        log(f"ERROR Reddit para '{keyword}': {e}")
    return results


SOURCES = [search_google_news, search_gdelt, search_reddit]


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("ERROR: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            log(f"ERROR Telegram: {r.status_code} {r.text}")
            return False
        return True
    except Exception as e:
        log(f"ERROR enviando a Telegram: {e}")
        return False


def format_alert(keyword, item):
    title = item["title"].replace("<", "").replace(">", "")
    fecha = ""
    dt = item.get("published_dt")
    if dt:
        # Se muestra en hora de Ecuador (UTC-5) para que sea fácil de leer.
        hora_ec = dt.astimezone(timezone(timedelta(hours=-5)))
        fecha = f"🕒 {hora_ec.strftime('%d/%m/%Y %H:%M')} (hora Ecuador)\n"
    return (
        f"🔔 <b>Alerta: {keyword}</b>\n"
        f"📰 {item['source']}\n"
        f"{fecha}"
        f"<b>{title}</b>\n"
        f"{item['url']}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if "--test" in sys.argv:
        log("Modo de prueba: enviando mensaje de prueba a Telegram...")
        ok = send_telegram_message(
            "✅ Prueba exitosa: tu bot de alertas está conectado correctamente."
        )
        log("Mensaje de prueba enviado." if ok else "FALLÓ el envío del mensaje de prueba.")
        sys.exit(0 if ok else 1)

    keywords = load_keywords()
    if not keywords:
        log("No hay palabras clave configuradas. Saliendo.")
        sys.exit(0)

    state = load_state()
    new_alerts = 0

    log(f"Monitoreando {len(keywords)} palabra(s) clave: {', '.join(keywords)}")

    for keyword in keywords:
        found_items = []
        for source_fn in SOURCES:
            items = source_fn(keyword)
            log(f"  {source_fn.__name__}: {len(items)} resultado(s) para '{keyword}'")
            found_items.extend(items)

        for item in found_items:
            if not item.get("url") or not item.get("title"):
                continue
            if not matches_keyword(item["title"], keyword):
                # Filtro extra: aseguramos que la palabra realmente aparezca en el título
                continue
            if not is_recent(item):
                # Descarta noticias viejas o sin fecha de publicación reconocible
                continue

            uid = item_id(item["url"], item["title"])
            if uid in state:
                continue  # ya se envió antes

            ok = send_telegram_message(format_alert(keyword, item))
            state[uid] = {"ts": time.time(), "keyword": keyword}
            if ok:
                new_alerts += 1
                log(f"Alerta enviada [{keyword}]: {item['title'][:80]}")
            time.sleep(1)  # evitar rate-limit de Telegram

    save_state(state)
    log(f"Corrida finalizada. Alertas nuevas enviadas: {new_alerts}")


if __name__ == "__main__":
    main()

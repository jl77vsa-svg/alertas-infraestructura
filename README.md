# Monitor de palabras clave → alertas a Telegram

Sistema 100% gratuito que rastrea la web en busca de tus palabras clave y te
avisa por Telegram apenas encuentra algo nuevo.

## Qué cubre y qué no

**Sí cubre (gratis, sin límites prácticos):**
- Noticias de miles de medios vía Google News RSS
- Base de datos global de noticias GDELT (actualizada cada ~15 min)
- Reddit (búsqueda pública)

**No cubre de forma gratuita** (limitación real de las plataformas, no del
sistema): Twitter/X, Facebook, Instagram, TikTok y LinkedIn cerraron el
acceso gratuito a búsqueda por palabra clave en sus APIs. Si más adelante
quieres cubrir alguna de estas, hay que evaluar una herramienta de pago
(Brandwatch, Mention, etc.) o el plan de pago de la API de X — pero eso ya
no sería gratuito.

## Cómo funciona

Un script en Python corre automáticamente cada 15 minutos usando
**GitHub Actions** (gratis para repositorios públicos, sin necesidad de
tener un servidor propio ni dejar tu computadora prendida). Cada corrida:

1. Busca tus palabras clave en las 3 fuentes.
2. Compara contra lo que ya se envió antes (para no repetir alertas).
3. Envía lo nuevo a tu chat de Telegram.
4. Guarda el registro para la próxima corrida.

## Instalación (una sola vez, ~10 minutos)

### 1. Crear el bot de Telegram

1. En Telegram, busca **@BotFather** y envíale `/newbot`.
2. Elige un nombre y un usuario (debe terminar en `bot`, ej. `MisAlertasBot`).
3. BotFather te dará un **token** (algo como `123456789:ABCdefGhIJKlmNoPQRs...`). Guárdalo.
4. Ahora busca **@userinfobot** en Telegram y envíale cualquier mensaje: te
   devuelve tu **Chat ID** (un número). Guárdalo también.
5. Importante: escríbele un mensaje cualquiera a tu bot recién creado (búscalo
   por su usuario y dale "Start"). Si no le escribes primero, el bot no puede
   enviarte mensajes.

### 2. Subir este proyecto a GitHub

1. Crea una cuenta gratuita en [github.com](https://github.com) si no tienes una.
2. Crea un repositorio nuevo (puede ser público o privado — público es más
   simple porque GitHub Actions es ilimitado y gratis ahí; privado también es
   gratis pero con minutos limitados al mes, que igual alcanzan de sobra para esto).
3. Sube todos los archivos de esta carpeta (`monitor.py`, `keywords.txt`,
   `requirements.txt`, `seen_state.json`, y la carpeta `.github/`).

### 3. Configurar tus credenciales en GitHub (sin exponerlas en el código)

1. En tu repositorio, ve a **Settings → Secrets and variables → Actions**.
2. Click en **New repository secret** y crea:
   - `TELEGRAM_BOT_TOKEN` → el token que te dio BotFather
   - `TELEGRAM_CHAT_ID` → tu Chat ID

### 4. Editar tus palabras clave

Abre `keywords.txt` y escribe una palabra o frase clave por línea. Ejemplo:

```
Ministerio de Infraestructura Ecuador
obras públicas Ecuador
vialidad Manabí
```

### 5. Activarlo

El workflow ya está configurado para correr solo cada 15 minutos. Para
probarlo de inmediato sin esperar:

1. Ve a la pestaña **Actions** de tu repositorio.
2. Selecciona **"Monitor de palabras clave"** en la lista de la izquierda.
3. Click en **"Run workflow"** → **Run workflow**.
4. En 1-2 minutos deberías recibir en Telegram cualquier coincidencia nueva.

A partir de ahí corre solo, 24/7, cada 15 minutos, sin que tengas que hacer nada.

## Ajustes opcionales

- **Frecuencia:** en `.github/workflows/monitor.yml`, cambia
  `*/15 * * * *` por ejemplo a `*/30 * * * *` para cada 30 minutos (recomendado
  si usas un repo privado, para consumir menos minutos gratuitos).
- **Idioma/país de las noticias:** en `monitor.py`, función
  `search_google_news`, puedes cambiar `lang="es-419"` y `country="EC"` por
  otros códigos si quieres priorizar noticias de otro país o idioma.
- **Agregar más palabras clave:** simplemente añade líneas a `keywords.txt`.
  Ten en cuenta que más palabras clave = más tiempo de ejecución por corrida
  (cada una se busca en las 3 fuentes).

## Notas sobre confiabilidad

- Google News, GDELT y Reddit no requieren API key, pero al ser endpoints no
  oficiales/públicos, ocasionalmente pueden cambiar de formato o bloquear
  temporalmente. El script está escrito para no caerse si una fuente falla
  (solo lo reporta en el log y sigue con las demás).
- El archivo `seen_state.json` se poda automáticamente (se olvidan alertas de
  más de 7 días) para que no crezca indefinidamente.
- Puedes revisar el historial de corridas y errores en la pestaña **Actions**
  de GitHub en cualquier momento.

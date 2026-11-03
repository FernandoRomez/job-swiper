# JobSwiper 💼🔥

Buscador de empleo estilo **Tinder** con IA. Scrapea ofertas de LinkedIn **usando tu propia sesión** (tú inicias sesión; nunca se guarda tu contraseña), te las muestra como tarjetas para deslizar, y al deslizar a la derecha genera un **Resume + CV + Cover Letter** adaptados a esa oferta, más el link para aplicar.

La IA puede venir de dos sitios (se elige en **Ajustes**):

| Proveedor | Qué necesitas | Cómo funciona |
|---|---|---|
| **API de Google Gemini** | Una API key | La app llama a la API REST desde tu servidor local. |
| **Claude por MCP** ⭐ | Nada — solo Claude Code o Antigravity abierto | La app te pasa cada petición a **ti** en el chat, a través del servidor MCP `jobswiper`. Sin API key y sin cuota. |

## Instalación

```bash
cd job-swiper
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium

# Tus datos (estos archivos están en .gitignore y nunca se suben)
cp perfil.example.json perfil.json    # nombre, email, teléfonos, educación
cp config.example.json config.json    # o exporta GEMINI_API_KEY=...
```

> 🔒 **Privacidad:** `config.json` (API key), `perfil.json` (datos de contacto), `*.db`,
> `resumes/`, `cola/` y `.linkedin-profile/` (sesión de LinkedIn) están en `.gitignore`.

## ⚡ Scrapear empleos con un botón

Arriba a la derecha, **⚡ Scrapear empleos** busca en todas las plataformas (OCC, Indeed,
Computrabajo, RemoteOK, Remotive, GetOnBoard, WeWorkRemotely, Arbeitnow, LinkedIn) y guarda
los resultados en la base de datos. Si dejas el puesto vacío, usa las búsquedas predeterminadas.
También desde terminal: `python3 -m studio.scrapers.run --sources all --query "PLC" --location Mexico`.

## Usar Claude por MCP (sin API key)

Un servidor MCP no puede *llamar* al modelo: es el modelo quien llama al servidor. Así que el
puente se invierte — JobSwiper **encola** cada petición de IA y espera, y tú (Claude) la **tomas**
y la **respondes** desde el chat:

```
app.py ──submit()──► [ cola de tareas ] ◄──tomar_tarea()── mcp_server.py ──► Claude
   ▲                                                                          │
   └──────────────── deliver() ◄── responder_tarea() ◄────────────────────────┘
```

### 1. Arranca la app y elige el proveedor

```bash
python3 app.py
```

En **Ajustes → ¿Quién pone la IA?** elige **🧠 Claude por MCP** y guarda. (O arranca con
`JOBSWIPER_AI=mcp python3 app.py`, que manda sobre lo guardado.)

### 2. Conecta el servidor MCP

**Claude Code** ya lo encuentra: el repo trae un `.mcp.json`. Abre Claude Code en esta carpeta y
aprueba el servidor `jobswiper` cuando te lo pregunte. Comprueba con `/mcp`.

**Antigravity, Claude Desktop, Cursor u otro cliente** — añade a su configuración de MCP:

```json
{
  "mcpServers": {
    "jobswiper": {
      "command": "python3",
      "args": ["/ruta/absoluta/a/job-swiper/mcp_server.py"],
      "env": { "JOBSWIPER_URL": "http://127.0.0.1:8777" }
    }
  }
}
```

El servidor solo usa la librería estándar de Python: no hay nada que instalar.

### 3. Trabaja en tiempo real desde el chat

Usa la web normalmente. Cuando algo necesite IA (generar documentos, AutoJob, el asistente…),
aparece abajo a la derecha **«Esperando a Claude»** y en tu chat basta con pedir:

> atiende las tareas de JobSwiper

Claude toma la tarea, la redacta y la devuelve; la web se desbloquea sola. También puedes
manejar la app entera desde el chat: *«busca controls engineer en Raleigh, dale like a los tres
mejores y genérame los CVs»*.

### Herramientas que expone el servidor MCP

| Herramienta | Para qué |
|---|---|
| `estado` | Proveedor activo, empleos, matches y tareas en espera. |
| `usar_mcp` | Cambia el proveedor de IA sin tocar la web. |
| `tareas_pendientes` / `tomar_tarea` | Ver la cola / recibir el prompt completo de la siguiente. |
| `responder_tarea` | Entregar el resultado (con `siguiente=true` encadena la siguiente). |
| `cancelar_tarea` | Descartar una tarea colgada (la web ve un error en vez de esperar). |
| `empleos` / `ver_empleo` | Listar y filtrar lo guardado. |
| `buscar_empleos` | Lanzar una búsqueda de Easy Apply en LinkedIn. |
| `swipe` | Dar like o descartar. |
| `generar_documentos` / `ver_documentos` | Generar y leer Resume + CV + Cover Letter. |
| `sugerir_titulos` | Proponer los mejores puestos para tu CV. |
| `perfil` | Leer o actualizar tu CV base y tu perfil. |
| `escribir_en_chat` | Dejar un mensaje en el chat de la web. |
| `linkedin_estado` | Ver o abrir la sesión de LinkedIn. |

`generar_documentos`, `sugerir_titulos` y las demás acciones que necesitan IA se lanzan **en
segundo plano** a propósito: si esperasen la respuesta HTTP se bloquearían contra tu propia
respuesta. Lánzalas y luego llama a `tomar_tarea`.

### Ajustes finos

- **Tiempo máx. de espera** (Ajustes, por defecto 900 s): lo que aguanta la web antes de darse
  por vencida. Si tardas más, la web muestra un error y puedes reintentar.
- Si reclamas una tarea y no la respondes en 4 minutos, vuelve sola a la cola.
- El puente vive en memoria: al reiniciar `app.py` la cola se vacía.

## Uso

```bash
python3 app.py
```

Abre **http://localhost:8777** y sigue el flujo:

1. **Perfil** — pega tu CV base y responde las preguntas iniciales (educación, experiencia, si te reubicas, etc.).
2. **Ajustes** — elige quién pone la IA: **Claude por MCP** (sin API key, ver arriba) o tu API key de Google Gemini (gratis en https://aistudio.google.com/apikey). Se guarda en `config.json`; también puedes definir `GOOGLE_API_KEY` como variable de entorno.
3. **Swipe → Conectar LinkedIn** — se abre un navegador real; inicia sesión tú mismo. La sesión queda guardada en `.linkedin-profile/`.
4. **Sugerir puestos** — la IA propone los mejores títulos de búsqueda para tu perfil.
5. **Buscar TODAS en LinkedIn** — scrapea de una vez todas las sugerencias (o pulsa 🔍 Buscar en una sola). Solo trae empleos de "Easy Apply". Desliza ♥ los que te interesen.
6. **Matches** — ahí aparecen tus documentos generados, con el link para aplicar. Recibes una notificación al terminar.
7. **Chat** — pregúntale a la IA sobre tus empleos guardados: "¿cuáles pagan más?", "¿cuáles no piden licenciatura?", "¿cuál encaja mejor con mi perfil?".

## Base de datos

Todo (empleos, swipes, documentos generados e historial del chat) se guarda de forma permanente en **`jobswiper.db`** (SQLite, se crea solo). Si cierras y vuelves a abrir la app, tus empleos, matches y documentos siguen ahí. El archivo está en `.gitignore` porque contiene datos personales; bórralo si quieres empezar de cero.

Tablas: `jobs` (con estado `new`/`liked`/`passed`), `documents` (resume, cv, cover letter por empleo) y `chat` (historial).

## Resolución de problemas

- **"Playwright no está instalado"** al conectar LinkedIn: instálalo para el mismo Python con el que ejecutas la app:
  ```bash
  python3 -m pip install --user playwright
  python3 -m playwright install chromium
  ```
- **El navegador se movía solo mientras iniciaba sesión**: corregido. El estado de sesión ahora se detecta leyendo la cookie `li_at` sin recargar la página.
- **La búsqueda "TODAS" tarda varios minutos**: es normal; visita cada oferta con pausas anti-bloqueo. Reduce el número de "Ofertas" si LinkedIn te pide verificación.

## Modo demo (sin LinkedIn ni Gemini)

```bash
JOBSWIPER_MOCK=1 python3 app.py
```

Usa datos de ejemplo para probar toda la interfaz sin conectar nada.

## Notas

- El scraping usa **tu sesión autenticada**; hazlo con moderación. Si LinkedIn detecta demasiadas peticiones puede pedirte verificación.
- La API key de Gemini nunca sale de tu equipo: las llamadas se hacen desde el servidor local en Python.
- El puente MCP solo escucha en `127.0.0.1`: nada sale de tu máquina y no necesita ninguna clave.
- `config.json` y `.linkedin-profile/` están en `.gitignore` (contienen datos personales).

# Job Studio — contrato compartido (lo leen TODOS los subagentes)

Nueva app independiente dentro de `job-swiper/studio/`. NO modificar app.py, postular.py, db.py ni static/ existentes
(solo importarlos). Python stdlib + playwright (ya instalado). UI en español. Datos del usuario en
../perfil.json (ver perfil.example.json). Nunca inventar experiencia en CVs.

## Estructura
```
studio/
  studio.py              # servidor HTTP (ThreadingHTTPServer), puerto 8790  -> agente BACKEND
  ai.py                  # generación CV/carta: Gemini si hay key en ../config.json, si no plantillas heurísticas -> BACKEND
  static/index.html, static/studio.css, static/studio.js     -> agente FRONTEND
  static/templates/*.css + static/templates/templates.js     -> agente PLANTILLAS
  scrapers/__init__.py, scrapers/<plataforma>.py, scrapers/run.py -> agente SCRAPERS
```

## Datos
- BD: `../jobswiper.db` vía `import db` (sys.path a job-swiper/). Tabla jobs(id,title,company,location,description,link,
  easy_apply,employment_type,seniority,query,source,status,created_at). applications(job_id,status,resume_pdf,cover_pdf,cover_text,detail).
- Ids: `<prefijo>_<id>` p.ej. li_123 (LinkedIn), occ_, ind_ (Indeed), ct_ (Computrabajo), rok_ (RemoteOK),
  rmt_ (Remotive), gob_ (GetOnBoard), wwr_ (WeWorkRemotely), arb_ (Arbeitnow). `source` = nombre de plataforma.
- Perfil maestro: `cola/hechas/*.json` / `cola/*.json` son ejemplos del formato DOC. ../config.json tiene base_cv (texto) y profile.

## Formato DOC (CV a la medida) — idéntico a cola/<id>.json de postular.py
```json
{"job_id":"li_1","idioma":"es|en","titular":"...","ubicacion":"...","perfil":"...",
 "competencias":[["Controles","PLC Siemens..."]],
 "experiencia":[{"puesto":"","empresa":"","fechas":"","bullets":[""]}],
 "proyectos":[{"titulo":"","bullets":[""]}],
 "educacion":[{"titulo":"","escuela":"","fechas":""}], "certs":["..."], "idiomas":"Español nativo · Inglés B2",
 "carta":"párrafos separados por \n\n", "respuestas":{}, "salario":"", "plantilla":"<id plantilla>"}
```
Contacto fijo: nombre/email/tel/linkedin/web = constantes de postular.py (NOMBRE, EMAIL, TEL_MX, TEL_US, LINKEDIN, WEB).

## API (JSON) de studio.py
- GET  /api/jobs?q=&source=&status=&country=   -> [{id,title,company,location,source,link,easy_apply,created_at,status,app_status,score}]
- GET  /api/jobs/<id>                           -> job completo + description + app (applications row) + doc (si existe)
- POST /api/jobs/<id>/status {status: new|liked|passed}
- GET  /api/profile                             -> {nombre,email,tel_mx,tel_us,linkedin,web, base: DOC base}
- POST /api/jobs/<id>/cv                        -> genera DOC a la medida (ai.py) y lo guarda en studio/docs/<id>.json; devuelve DOC
- PUT  /api/jobs/<id>/doc  {DOC}                -> guarda ediciones del usuario
- POST /api/jobs/<id>/cover                     -> genera carta; devuelve {carta}
- POST /api/jobs/<id>/pdf {kind:"cv"|"cover", html:"<doc html completo>"} -> renderiza con playwright a resumes/<id>/..pdf, devuelve {path,url}
- GET  /files/<ruta relativa a job-swiper/resumes>  -> sirve PDFs
- POST /api/jobs/<id>/apply                     -> copia DOC a ../cola/<id>.json y lanza `python3 postular.py abrir <id>` en segundo plano (solo LinkedIn); para otras fuentes devuelve {open_url: link}
- POST /api/scrape {sources:[...], query, location} -> lanza scrapers/run.py en hilo; GET /api/scrape/status -> {running, log[], counts}
- GET  /api/stats -> {total, by_source, by_status, applied}
- GET  /api/templates -> lista leída de static/templates (opcional; el frontend también usa templates.js)

## Plantillas (frontend)
`static/templates/templates.js` define `window.STUDIO_TEMPLATES = [{id, name, kind:'cv'|'cover', css:'templates/<id>.css', render(doc, contact) -> html string (solo <body> interior)}]`.
El frontend monta el html en una "hoja" A4 (`<div class="page" contenteditable>`) dentro de un iframe; la clase raíz `.tpl-<id>`.
Para PDF el frontend manda el HTML completo (`<!doctype html>` + `<style>` con el css de la plantilla inline + body) a /api/jobs/<id>/pdf.
Las plantillas deben marcar campos editables con `data-field="perfil"`, `data-field="experiencia.0.bullets.1"` etc. para que las ediciones vuelvan al DOC.

## Módulos extra (fase 2) — sistema de plugins
Cada módulo es independiente para no chocar con studio.py/studio.js:
- Backend: `studio/extras/<mod>.py` con `def handle(method, path, query, body) -> (status:int, obj) | None`
  (None = no es mi ruta). Rutas bajo `/api/x/<mod>/...`. Puede crear tablas propias en ../jobswiper.db
  (prefijo `x_<mod>_`) usando sqlite3 directo; leer jobs/applications vía `db`. studio.py (integración final) importará
  todos los `extras/*.py` y llamará `handle` antes de su propio router.
- Frontend: `studio/static/modules/<mod>.js` (+ `<mod>.css` opcional) que haga
  `(window.STUDIO_MODULES ||= []).push({id, title, icon, scope:'global'|'job', mount(el, ctx)})`.
  scope 'global' = vista propia en la barra lateral; 'job' = pestaña extra al abrir un empleo.
  ctx = {jobId, job, doc, api(path, opts)->Promise<json>, toast(msg), refresh()}.
  Usar variables CSS --bg --fg --muted --accent --card --border con fallback.

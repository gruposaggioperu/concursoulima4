# Control de Impuestos — despliegue en Cloudflare

## Si Cloudflare dice “use wrangler deploy instead”

El **subidor por arrastre** del panel (Upload assets) **no sirve** para esta carpeta: incluye `wrangler.toml` y un **Worker** (`worker/index.js`).

**No subas el ZIP manualmente.** Despliega desde la terminal con los pasos de la sección **Paso 3** más abajo (`npm install` → `npx wrangler login` → `npx wrangler deploy`).

---

Esta carpeta contiene **todo lo necesario** para publicar la app en **Cloudflare Workers** (interfaz) y conectar el **backend Python** (SUNAT + BCRP).

## Arquitectura (importante)

| Parte | Dónde corre | Por qué |
|--------|-------------|---------|
| **Frontend** (`public/index.html`) | Cloudflare Workers + Assets | Rápido, CDN global |
| **API** (`/api/login`, `/api/consulta`, …) | **Servidor Python** (no puede ir solo como HTML) | OAuth SUNAT, ZIP/CSV, sesiones |

Cloudflare **no ejecuta** tu `server.py` como un VPS tradicional. Por eso el Worker **reenvía** `/api/*` a una URL pública del backend (`API_ORIGIN`).

### Tres formas de alojar el backend

1. **PC de oficina + Cloudflare Tunnel** (sin abrir puertos en el router) — ver `tunnel/`
2. **Fly.io / Railway / VPS** con Docker — ver `backend/Dockerfile`
3. **Mismo PC** solo para pruebas — `API_ORIGIN=http://127.0.0.1:8080` (solo con `wrangler dev` en local)

---

## Requisitos

- Cuenta en [Cloudflare](https://dash.cloudflare.com/)
- [Node.js](https://nodejs.org/) 18+ (para Wrangler)
- Python 3.10+ (para el backend)
- Credenciales SUNAT en `backend/config.json` (copia desde `config.example.json`)

---

## Paso 1 — Preparar archivos

Desde `App_Impuestos`:

```bat
cloudflare_deploy\scripts\preparar.bat
```

Copia `index.html`, Python y datos actualizados a esta carpeta.

Configura el backend:

```bat
copy cloudflare_deploy\backend\config.example.json cloudflare_deploy\backend\config.json
```

Edita `backend\config.json` con RUC, usuario SOL, `client_id` y `client_secret` SUNAT.

---

## Paso 2 — Levantar el backend (API)

### Opción A — Local (pruebas)

```bat
cd cloudflare_deploy\backend
pip install -r requirements.txt
python server.py
```

Debe responder: `http://127.0.0.1:8080/api/version`

### Opción B — Docker (VPS / Fly.io)

```bash
cd cloudflare_deploy/backend
docker build -t control-impuestos-api .
docker run -p 8080:8080 -v "%cd%/config.json:/app/config.json" control-impuestos-api
```

En **Fly.io**: renombra `fly.toml.example` → `fly.toml`, sube `config.json` como secreto o volumen, luego `fly deploy`.

Anota la **URL pública HTTPS** del backend (ej. `https://control-impuestos-api.fly.dev`).

### Opción C — Túnel desde tu PC

Ver `tunnel/LEEME-tunnel.md`.

---

## Paso 3 — Desplegar en Cloudflare

```bat
cd cloudflare_deploy
npm install
copy .dev.vars.example .dev.vars
```

Edita `.dev.vars` con tu backend:

```
API_ORIGIN=https://TU-BACKEND-PUBLICO
```

Prueba local (frontend en Cloudflare dev + API remota):

```bat
npm run dev
```

Inicia sesión en Cloudflare:

```bat
npx wrangler login
```

Configura el secreto en producción (no subas `.dev.vars` a git):

```bat
npx wrangler secret put API_ORIGIN
```

Pega la URL HTTPS del backend cuando lo pida.

Publicar:

```bat
npm run deploy
```

Wrangler mostrará la URL, por ejemplo: `https://control-impuestos.TU-USUARIO.workers.dev`

---

## Paso 4 — Verificar

1. Abre la URL del Worker en el navegador.
2. Debe cargar el login.
3. Ingresa credenciales SOL → **Cargar período** debe devolver datos SUNAT.

Si ves *"API no configurada"*, falta `API_ORIGIN` en secretos de Cloudflare.

Si el login falla con 502, el backend no es alcanzable desde Internet (revisa túnel, firewall o Fly).

---

## Estructura de la carpeta

```
cloudflare_deploy/
  LEEME.md                 ← esta guía
  wrangler.toml            ← configuración Cloudflare Worker
  package.json
  worker/index.js          ← sirve HTML + proxy /api/*
  public/
    index.html             ← dashboard
    data/uit_historico.json
  backend/                 ← API Python (SUNAT)
    server.py
    sunat_sire.py
    bcrp_indicadores.py
    config.example.json
    Dockerfile
  tunnel/                  ← túnel Cloudflare → PC local
  scripts/preparar.bat     ← sincroniza desde App_Impuestos
```

---

## Seguridad

- **No subas** `backend/config.json` con claves a repositorios públicos.
- Usa **HTTPS** siempre en `API_ORIGIN`.
- Restringe quién conoce la URL del Worker; contiene acceso al login SUNAT.
- Rota claves SOL si expusiste la URL por error.

---

## Actualizar la app

1. `scripts\preparar.bat`
2. Redespliega backend (Docker/Fly/PC)
3. `npm run deploy` en `cloudflare_deploy`

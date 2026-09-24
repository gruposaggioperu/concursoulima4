# Paso a paso — Control de Impuestos en tu dominio (Cloudflare)

Ejemplo de nombres (cámbialos por los tuyos):

| Uso | Subdominio ejemplo |
|-----|---------------------|
| Dashboard (Worker) | `impuestos.tudominio.com` |
| API Python (túnel desde PC) | `api.tudominio.com` |
| Web actual (no tocar) | `www.tudominio.com` |

Recomendación: **no reemplaces** la página principal; usa un **subdominio** solo para impuestos.

---

## Fase 0 — Requisitos

- [ ] Dominio en **Cloudflare** (DNS administrado por Cloudflare).  
  Si el dominio está en otro registrador: en Cloudflare → **Add site** → cambia los nameservers a los que te indique Cloudflare.
- [ ] [Node.js](https://nodejs.org/) 18+ instalado en tu PC.
- [ ] Python 3.10+ en el PC que correrá la API SUNAT (puede ser el mismo).
- [ ] Carpeta del proyecto: `App_Impuestos\cloudflare_deploy`.

---

## Fase 1 — Backend Python (API SUNAT)

### 1.1 Configuración

```bat
cd "...\App_Impuestos\cloudflare_deploy\backend"
copy config.example.json config.json
```

Edita `config.json` (RUC, usuario SOL, `client_id`, `client_secret`).

### 1.2 Probar en local

```bat
pip install -r requirements.txt
python server.py
```

Abre: http://127.0.0.1:8080/api/version → debe verse `"ok": true`.

Deja esta ventana abierta (o usa `iniciar.bat` del proyecto principal en el puerto 8080).

---

## Fase 2 — Exponer la API con tu dominio (Cloudflare Tunnel)

Así la API queda en **https://api.tudominio.com** sin abrir puertos en el router.

### 2.1 Instalar cloudflared

```bat
winget install Cloudflare.cloudflared
```

### 2.2 Iniciar sesión y crear túnel

```bat
cloudflared tunnel login
cloudflared tunnel create control-impuestos-api
```

Anota:

- **Tunnel ID** (UUID)
- Archivo de credenciales, ej.:  
  `C:\Users\TU_USUARIO\.cloudflared\UUID.json`

### 2.3 Archivo de configuración del túnel

En `cloudflare_deploy\tunnel\` copia `config.example.yml` → `config.yml`:

```yaml
tunnel: TU-TUNNEL-ID
credentials-file: C:\Users\TU_USUARIO\.cloudflared\TU-TUNNEL-ID.json

ingress:
  - hostname: api.tudominio.com
    service: http://127.0.0.1:8080
  - service: http_status:404
```

### 2.4 DNS en Cloudflare (panel web)

1. **Websites** → tu dominio → **DNS** → **Add record**
2. Tipo **CNAME**
3. Name: `api`
4. Target: `TU-TUNNEL-ID.cfargotunnel.com`
5. Proxy: **Proxied** (nube naranja)
6. Save

### 2.5 Enrutar el túnel al hostname

```bat
cd "...\cloudflare_deploy\tunnel"
cloudflared tunnel route dns control-impuestos-api api.tudominio.com
```

(O usa el paso 2.4 manual si ya creaste el CNAME.)

### 2.6 Ejecutar túnel (PC encendido)

Con `python server.py` corriendo en 8080:

```bat
cloudflared tunnel --config config.yml run
```

Prueba en el navegador: **https://api.tudominio.com/api/version**

Para dejar el túnel como servicio Windows, consulta la doc de Cloudflare “Run as a service”.

---

## Fase 3 — Desplegar el dashboard (Worker + Wrangler)

**No uses “Upload” en el panel.** Usa la terminal.

### 3.1 Instalar y entrar

```bat
cd "...\App_Impuestos\cloudflare_deploy"
npm install
npx wrangler login
```

### 3.2 Secreto: dónde está la API

```bat
npx wrangler secret put API_ORIGIN
```

Cuando pida el valor, escribe **solo** (sin barra final):

```
https://api.tudominio.com
```

### 3.3 Primer despliegue

```bat
npm run deploy
```

Copia la URL temporal `https://control-impuestos.xxxx.workers.dev` y prueba que abre el login.  
Si falla la API, revisa `API_ORIGIN` y que el túnel + `server.py` estén activos.

### 3.4 Conectar tu dominio al Worker

**Opción A — Panel (más visual)**

1. Cloudflare → **Workers & Pages** → worker **control-impuestos**
2. **Settings** → **Domains & Routes** → **Add** → **Custom domain**
3. Escribe: `impuestos.tudominio.com`
4. Confirma (Cloudflare crea el DNS si hace falta)

**Opción B — wrangler.toml** (descomenta y ajusta en `wrangler.toml`):

```toml
routes = [
  { pattern = "impuestos.tudominio.com", custom_domain = true }
]
```

Luego: `npm run deploy`

### 3.5 Probar en producción

1. https://impuestos.tudominio.com → pantalla de login  
2. Ingresar SOL → **Cargar período** → datos SUNAT  

---

## Fase 4 — Tu página web actual

- **www.tudominio.com** sigue igual (Pages, hosting anterior, etc.).
- Solo añadiste registros DNS nuevos (`api`, `impuestos`).
- No borres registros existentes de `www` o `@` salvo que sepas qué hacen.

---

## Resumen del flujo

```
Usuario → https://impuestos.tudominio.com
              ↓ (Cloudflare Worker)
         index.html + /api/* proxy
              ↓ API_ORIGIN
         https://api.tudominio.com
              ↓ (Cloudflare Tunnel)
         PC local :8080 → server.py → SUNAT
```

---

## Problemas frecuentes

| Síntoma | Qué revisar |
|---------|-------------|
| “use wrangler deploy” | No subas ZIP al panel; usa `npm run deploy` |
| “API no configurada” | `npx wrangler secret put API_ORIGIN` |
| 502 al login | Túnel caído, `server.py` apagado, o `API_ORIGIN` incorrecto |
| SSL / DNS pending | Espera 5–15 min; DNS proxied en Cloudflare |
| Conflicto con web actual | Usa subdominio `impuestos.`, no el apex `@` |

---

## Actualizar la app

```bat
App_Impuestos\cloudflare_deploy\scripts\preparar.bat
cd cloudflare_deploy
npm run deploy
```

Reinicia `server.py` / túnel si cambiaste el backend Python.

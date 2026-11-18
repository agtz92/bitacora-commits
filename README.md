# Bitácora de commits

Registro de los commits de todos tus repositorios de GitHub, por mes, con cliente y etiquetas por repositorio,
y un resumen exportable con el SHA de cada commit (ej. «mexgamer: 6 commits en mayo 2026: `100a569`, …»).

Corre en **Vercel** (Python/FastAPI) con **Postgres en Supabase**, e incluye un **servidor MCP** para consultarla
desde Claude.

## Qué hace

- **Matriz**: repositorio × mes con el número de commits. Al tocar el nombre de un repo se abre un menú para
  asignarle **uno o varios clientes** y **etiquetas**; «Ver commits por día» muestra fecha, hora, SHA y mensaje.
- **Resumen**: filtra por rango de meses, cliente, etiqueta o repositorio y obtén, por repo y mes, cuántos commits
  hubo y sus SHAs. Copia como Markdown o descarga Markdown/CSV.
- **Buscar repos y commits nuevos** (on-demand): pregunta a GitHub por todos los repos del token (propios, de
  colaborador y de organizaciones), agrega los nuevos y descarga los commits nuevos de todas las ramas. Trabaja en
  pasos de ~40 s (límite de Vercel); la página los encadena sola. «Revisar todo» revisa todos los repos.
- Commits de bots (github-actions, GitHub Classroom, Vercel, dependabot) se guardan pero se excluyen por defecto.
- Fechas en hora de Ciudad de México (configurable).

## Acceso

- **Web**: pide contraseña (`APP_PASSWORD`). La sesión dura 30 días en ese navegador. Cambiar la contraseña
  cierra todas las sesiones.
- **MCP**: `https://<dominio>/mcp/<MCP_SECRET>`. La URL completa es la credencial: no pide contraseña, así que
  trátala como una contraseña. Para revocarla, cambia `MCP_SECRET` en Vercel y vuelve a desplegar.

### Conectar a Claude

claude.ai → Settings → Connectors → **Add custom connector** → pega la URL del MCP. Herramientas:

| Herramienta | Uso |
|---|---|
| `resumen_commits` | Por repo y mes: número de commits y SHAs (Markdown, JSON o CSV) |
| `matriz_mensual` | Commits por repo y mes, sin SHAs |
| `listar_repositorios` | Repos con clientes, etiquetas, total y primer/último commit |
| `commits_de_repositorio` | Commits de un repo con SHA completo y mensaje |
| `clientes_y_etiquetas` | Clientes y etiquetas existentes |
| `asignar_clientes_etiquetas` | Agregar, quitar o reemplazar clientes/etiquetas de uno o varios repos |
| `sincronizar_github` | Buscar repos y commits nuevos (puede requerir varias llamadas) |

## Despliegue

1. **Base de datos**: ejecuta `sql/schema.sql` en Supabase y crea un rol con acceso solo a ese schema:

   ```sql
   create role bitacora_app login password '…' connection limit 20;
   grant usage on schema bitacora to bitacora_app;
   grant select, insert, update, delete on all tables in schema bitacora to bitacora_app;
   grant usage, select on all sequences in schema bitacora to bitacora_app;
   -- si las tablas tienen RLS activado:
   -- create policy app_all on bitacora.<tabla> for all to bitacora_app using (true) with check (true);
   ```

2. **Vercel**: importa este repo. `vercel.json` ya define la función Python, la región (`sfo1`, junto a
   Supabase us-west-1) y el límite de 60 s. Configura las variables de `.env.example`.
3. Entra a la página, pulsa **Buscar repos y commits nuevos** y espera a que termine.

### Token

GitHub → Settings → Developer settings → Personal access tokens.

- **Fine-grained (recomendado)**: Resource owner = tu usuario, «All repositories», *Contents: Read-only* y
  *Metadata: Read-only*.
- Los repos de una organización (ej. `Tecnologico-de-Monterrey-MNA`) solo aparecen con un token fine-grained si la
  organización lo permite. Si no, usa un **token classic** con `repo` y `read:org` (la app solo lee).

## Clientes y etiquetas

Se guardan en la base de datos. Pie de página: **Exportar** (JSON) e **Importar** (mismo formato:
`{"repo": {"clients": [...], "tags": [...]}}`). Los repos aún no descubiertos quedan pendientes y se aplican al
sincronizar. Las etiquetas se guardan en minúsculas; los clientes respetan mayúsculas.

## API (requiere sesión)

| Método | Ruta | Uso |
|---|---|---|
| GET | `/api/matrix?bots=false` | Matriz repo × mes |
| GET | `/api/repos/{id}/commits` | Commits de un repo |
| PUT | `/api/repos/{id}/meta` | `{"clients": [...], "tags": [...]}` |
| POST | `/api/sync/start?force=false` | Lista repos y marca los que hay que revisar |
| POST | `/api/sync/step` | Descarga commits ~40 s; repetir mientras `running` |
| GET | `/api/sync` | Estado de la última revisión |
| GET | `/api/summary` | `month_from`, `month_to` (YYYY-MM), `client`, `tag`, `repo`, `bots`, `format=json\|md\|csv` |
| GET/POST | `/api/meta/export`, `/api/meta/import` | Clientes y etiquetas en JSON |

`client=__none` / `tag=__none` filtran los repos sin cliente / sin etiqueta.

## Desarrollo local

```bash
pip install -r requirements.txt uvicorn
export DATABASE_URL=… GITHUB_TOKEN=… APP_PASSWORD=… SESSION_SECRET=… MCP_SECRET=…
uvicorn app.main:app --reload
python -m app.cli sync            # o summary / export-meta / import-meta
```

## Notas

- Un repo renombrado se reconoce por su id de GitHub y conserva clientes y etiquetas.
- Un repo que deja de aparecer en la cuenta se oculta; sus commits no se borran.
- Por rama solo se piden commits si su último SHA cambió, y se deja de paginar al llegar a commits conocidos.

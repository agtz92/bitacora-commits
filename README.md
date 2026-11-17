# Bitácora de commits

Registro de los commits de todos tus repositorios de GitHub, por mes, con cliente y etiquetas por repositorio,
y un resumen exportable con el SHA de cada commit (ej. «mexgamer: 6 commits en mayo 2026: `a1b2c3d`, …»).

Corre en Docker en tu Mac. La base de datos (SQLite) y los espejos git viven en un volumen, así que sobreviven a
reinicios y actualizaciones.

## Qué hace

- **Matriz**: repositorio × mes con el número de commits. Toca un repo para ver cada día, hora, SHA y mensaje,
  y para asignarle **uno o varios clientes** y **etiquetas**.
- **Resumen**: filtra por rango de meses, cliente, etiqueta o repositorio y obtén, por repo y por mes, cuántos
  commits hubo y sus SHAs (con enlace a GitHub). Copia como Markdown o descarga Markdown/CSV.
- **Buscar repos y commits nuevos** (on-demand): pregunta a GitHub por todos los repos a los que tiene acceso el
  token (propios, de colaborador y de organizaciones como la del Tec), agrega los nuevos y descarga solo los commits
  de repos que tuvieron push desde la última revisión. «Revisar todo» vuelve a revisar todos los repos.
- Cuenta commits de **todas las ramas** y deduplica por SHA. Los commits de bots (github-actions, GitHub Classroom,
  Vercel, dependabot) se guardan pero se excluyen por defecto.
- Las fechas se muestran en hora de Ciudad de México (configurable).

## Instalación en la Mac

Requisitos: Docker Desktop, OrbStack o Colima.

```bash
git clone https://github.com/agtz92/bitacora-commits.git
cd bitacora-commits
cp .env.example .env
# edita .env y pega tu GITHUB_TOKEN
docker compose up -d --build
```

Abre <http://localhost:8787> y pulsa **Buscar repos y commits nuevos**. La primera vez tarda unos minutos
(descarga el historial de cada repo, sin el contenido de los archivos). Las siguientes tardan segundos.

Desde otra computadora en tu red: `http://<IP-de-la-Mac>:8787`. Si la vas a exponer fuera de tu red, define
`BASIC_AUTH_USER` y `BASIC_AUTH_PASSWORD` en `.env`.

### Token

Crea un token en GitHub → Settings → Developer settings → Personal access tokens.

- **Fine-grained (recomendado)**: Resource owner = tu usuario, «All repositories», permisos *Contents: Read-only*
  y *Metadata: Read-only*. Es de solo lectura.
- Los repos de una organización (ej. `Tecnologico-de-Monterrey-MNA`) solo aparecen con un token fine-grained si la
  organización lo permite. Si no aparecen, usa un **token classic** con el scope `repo` (y `read:org`). Ten en cuenta
  que el scope `repo` da también permiso de escritura; la app solo lee.

## Clientes y etiquetas

Al arrancar por primera vez se importan los clientes y etiquetas de `seed/meta.json` (exportados del artifact de
claude.ai). Los de repos que todavía no se han descubierto quedan pendientes y se aplican en la primera sincronización.

Las etiquetas se guardan en minúsculas; los clientes respetan mayúsculas. Botones rápidos: Hulempak, HEQ, PHASA,
DRR y Personal (puedes escribir cualquier otro).

## Línea de comandos

```bash
docker compose exec bitacora python -m app.cli sync                 # buscar repos y commits nuevos
docker compose exec bitacora python -m app.cli sync --force         # revisar todos los repos
docker compose exec bitacora python -m app.cli summary --from 2026-05 --to 2026-05
docker compose exec bitacora python -m app.cli summary --from 2026-01 --to 2026-06 --client Hulempak --format csv > hulempak.csv
docker compose exec bitacora python -m app.cli summary --tag seo --no-messages
docker compose exec bitacora python -m app.cli export-meta > meta.json
docker compose exec bitacora python -m app.cli import-meta seed/meta.json --overwrite
docker compose exec bitacora python -m app.cli backup /data/backup.sqlite3
docker compose cp bitacora:/data/backup.sqlite3 ./backup.sqlite3
```

## API

| Método | Ruta | Uso |
|---|---|---|
| GET | `/api/matrix?bots=false` | Matriz repo × mes con clientes y etiquetas |
| GET | `/api/repos/{id}/commits` | Commits de un repo (fecha, hora, SHA, mensaje) |
| PUT | `/api/repos/{id}/meta` | `{"clients": [...], "tags": [...]}` |
| POST | `/api/sync?force=false` | Inicia la búsqueda de repos y commits nuevos |
| GET | `/api/sync` | Estado y resultado de la última revisión |
| GET | `/api/summary` | Resumen. Parámetros: `month_from`, `month_to` (YYYY-MM), `client`, `tag`, `repo`, `bots`, `format=json\|md\|csv` |
| GET | `/api/meta/export` | Clientes y etiquetas en JSON |

`client=__none` / `tag=__none` filtran los repos sin cliente / sin etiqueta.

## Configuración (`.env`)

| Variable | Default | |
|---|---|---|
| `GITHUB_TOKEN` | — | Obligatorio |
| `PORT` | `8787` | Puerto en la Mac |
| `SINCE` | `2025-01-01` | Commits anteriores no se guardan |
| `TIMEZONE` | `America/Mexico_City` | Zona para agrupar por día y mes |
| `BOT_AUTHORS` | ver `.env.example` | Autores que cuentan como bots |
| `INCLUDE_FORKS` | `true` | Los repos de GitHub Classroom son forks |
| `EXCLUDE_REPOS` | vacío | Repos a ignorar |
| `AUTO_SYNC_HOURS` | `0` | 0 = solo on-demand; N = revisar sola cada N horas |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | vacío | Protege la página |

## Datos

Todo vive en el volumen `bitacora-data` (`/data` dentro del contenedor):

- `bitacora.sqlite3`: repos, commits, clientes, etiquetas e historial de revisiones.
- `mirrors/<id>.git`: espejos git parciales (solo historial) usados para leer los commits.

Actualizar a una versión nueva: `git pull && docker compose up -d --build`. Los datos se conservan.

Notas:

- Si un repo se renombra, se reconoce por su id de GitHub y conserva sus clientes y etiquetas.
- Si un repo deja de aparecer en la cuenta, se oculta pero sus commits no se borran.
- Si reescribes la historia de una rama (force push), los commits que ya estaban registrados se conservan.

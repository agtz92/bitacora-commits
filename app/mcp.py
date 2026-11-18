"""Servidor MCP (transporte HTTP "streamable", sin estado) con herramientas sobre la bitácora."""
import json

from . import db, summary, sync
from .config import settings

PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = (
    "Bitácora de commits de GitHub de Alfredo (cuenta agtz92), desde 2025, con hora de Ciudad de México. "
    "Cada repositorio puede tener varios clientes (Hulempak, HEQ, PHASA, DRR, Personal, …) y etiquetas, y para "
    "cada cliente un proyecto: el nombre con que ese cliente reconoce el trabajo (varios repos pueden formar un "
    "proyecto, y un repo puede llamarse distinto para cada cliente). "
    "Usa resumen_commits para materialidad: con cliente, agrupa por proyecto y mes con los SHAs y el repo de cada "
    "commit. "
    "Los commits de bots se excluyen salvo que se pida. Meses en formato YYYY-MM. "
    "Si los datos parecen desactualizados, llama a sincronizar_github (puede requerir varias llamadas)."
)

_month = {"type": "string", "description": "Mes en formato YYYY-MM", "pattern": r"^\d{4}-\d{2}$"}

TOOLS = [
    {
        "name": "resumen_commits",
        "description": "Resumen de commits con SHAs (fecha, hora y mensaje opcional). Con cliente: por proyecto y mes, "
                       "indicando el repo de cada commit; sin cliente: por repositorio y mes. Filtra por rango de meses, "
                       "cliente, proyecto, etiqueta o repositorio. Ideal para evidencia de trabajo.",
        "inputSchema": {"type": "object", "properties": {
            "mes_desde": _month, "mes_hasta": _month,
            "cliente": {"type": "string", "description": "Cliente exacto (ej. Hulempak). '__none' = sin cliente. "
                                                         "Con cliente, el resultado se agrupa por proyecto."},
            "proyecto": {"type": "string", "description": "Proyecto del cliente (requiere cliente). '__none' = sin proyecto."},
            "etiqueta": {"type": "string", "description": "Etiqueta (ej. seo). '__none' = sin etiqueta."},
            "repositorio": {"type": "string", "description": "Nombre o owner/nombre"},
            "incluir_bots": {"type": "boolean", "default": False},
            "incluir_mensajes": {"type": "boolean", "default": True},
            "formato": {"type": "string", "enum": ["markdown", "json", "csv"], "default": "markdown"},
        }},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "matriz_mensual",
        "description": "Número de commits por mes (sin SHAs), por proyecto si se da cliente o por repositorio si no.",
        "inputSchema": {"type": "object", "properties": {
            "mes_desde": _month, "mes_hasta": _month,
            "cliente": {"type": "string"}, "proyecto": {"type": "string"}, "etiqueta": {"type": "string"},
            "incluir_bots": {"type": "boolean", "default": False},
        }},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "listar_repositorios",
        "description": "Repositorios con sus clientes (y proyecto de cada cliente), etiquetas, total de commits y "
                       "primer/último commit. "
                       "Filtra por texto, cliente o etiqueta.",
        "inputSchema": {"type": "object", "properties": {
            "buscar": {"type": "string", "description": "Texto contenido en el nombre"},
            "cliente": {"type": "string"}, "etiqueta": {"type": "string"},
            "incluir_sin_commits": {"type": "boolean", "default": False},
        }},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "commits_de_repositorio",
        "description": "Lista de commits de un repositorio (fecha, hora, SHA completo, autor y mensaje).",
        "inputSchema": {"type": "object", "required": ["repositorio"], "properties": {
            "repositorio": {"type": "string"}, "mes_desde": _month, "mes_hasta": _month,
            "incluir_bots": {"type": "boolean", "default": False},
        }},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "clientes_y_etiquetas",
        "description": "Clientes con sus proyectos (y repos de cada uno) y etiquetas existentes.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "asignar_clientes_etiquetas",
        "description": "Cambia los clientes y/o etiquetas de uno o varios repositorios. "
                       "modo 'agregar' (por defecto) suma a lo existente, 'quitar' elimina, 'reemplazar' sustituye.",
        "inputSchema": {"type": "object", "required": ["repositorios"], "properties": {
            "repositorios": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "clientes": {"type": "array", "items": {"type": "string"}},
            "etiquetas": {"type": "array", "items": {"type": "string"}},
            "modo": {"type": "string", "enum": ["agregar", "quitar", "reemplazar"], "default": "agregar"},
        }},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "asignar_proyecto",
        "description": "Asigna a uno o varios repositorios el proyecto con que un cliente los reconoce "
                       "(ej. backend_ y frontend_website_cms_generator → «Generador de sitios web» para Hulempak). "
                       "Si el cliente no estaba asignado al repo, se agrega. proyecto vacío quita el proyecto.",
        "inputSchema": {"type": "object", "required": ["repositorios", "cliente", "proyecto"], "properties": {
            "repositorios": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "cliente": {"type": "string"},
            "proyecto": {"type": "string"},
        }},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "sincronizar_github",
        "description": "Busca repos nuevos en GitHub y descarga commits nuevos. Trabaja ~40 s por llamada; "
                       "si responde que faltan repos, vuelve a llamarla. forzar=true revisa todos los repos.",
        "inputSchema": {"type": "object", "properties": {"forzar": {"type": "boolean", "default": False}}},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
    },
]


def _f(a: dict) -> summary.Filters:
    return summary.Filters(a.get("mes_desde") or None, a.get("mes_hasta") or None, a.get("cliente") or None,
                           a.get("etiqueta") or None, a.get("repositorio") or None, bool(a.get("incluir_bots")),
                           (a.get("proyecto") or None) if a.get("cliente") else None)


def _resumen(a):
    with db.session() as conn:
        data = summary.query(conn, _f(a))
    fmt = a.get("formato", "markdown")
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, default=str)
    if fmt == "csv":
        return summary.to_csv(data)
    return summary.to_markdown(data, a.get("incluir_mensajes", True))


def _matriz(a):
    with db.session() as conn:
        data = summary.query(conn, _f(a))
    if not data["repos"]:
        return "Sin commits con esos filtros."
    lines = [f"Total: {data['total']} commits. Por mes: "
             + ", ".join(f"{k}: {v}" for k, v in data["by_month"].items()), ""]
    if data["projects"]:
        for g in data["projects"]:
            months = ", ".join(f"{m['month']}: {m['count']}" for m in g["months"])
            lines.append(f"- {g['project']} ({g['total']}; repos: {', '.join(x.split('/')[-1] for x in g['repos'])}): {months}")
        return "\n".join(lines)
    for r in data["repos"]:
        meta = " | ".join(x for x in [", ".join(r["clients"]), ", ".join(r["tags"])] if x)
        months = ", ".join(f"{m['month']}: {m['count']}" for m in r["months"])
        lines.append(f"- {r['full_name']} ({r['total']}){' [' + meta + ']' if meta else ''}: {months}")
    return "\n".join(lines)


def _listar(a):
    q = (a.get("buscar") or "").lower()
    with db.session() as conn:
        meta = db.get_meta(conn)
        rows = conn.execute(
            """select r.id, r.full_name, r.name, r.private, r.created_at, r.last_error,
                      count(c.sha) filter (where not c.is_bot) as total,
                      min(c.local_date) filter (where not c.is_bot) as first,
                      max(c.local_date) filter (where not c.is_bot) as last
               from bitacora.repos r left join bitacora.commits c on c.repo_id = r.id
               where not r.gone group by r.id order by lower(r.name)""").fetchall()
    out = []
    for r in rows:
        m = meta.get(r["id"], {"clients": [], "tags": [], "projects": {}})
        if q and q not in r["name"].lower():
            continue
        if not a.get("incluir_sin_commits") and not r["total"]:
            continue
        cl, tg = a.get("cliente"), a.get("etiqueta")
        if cl == "__none" and m["clients"] or cl and cl != "__none" and cl.lower() not in [x.lower() for x in m["clients"]]:
            continue
        if tg == "__none" and m["tags"] or tg and tg != "__none" and tg.lower() not in m["tags"]:
            continue
        span = f" del {r['first']} al {r['last']}" if r["total"] else ""
        cl_txt = ", ".join(f"{c} (proyecto: {m['projects'][c]})" if c in m["projects"] else c for c in m["clients"])
        out.append(f"- {r['full_name']}{' (privado)' if r['private'] else ''}: {r['total']} commits"
                   f"{span}"
                   f" · clientes: {cl_txt or '—'}"
                   f" · etiquetas: {', '.join(m['tags']) or '—'}"
                   f"{' · ⚠ ' + r['last_error'] if r['last_error'] else ''}")
    return f"{len(out)} repositorios\n" + "\n".join(out) if out else "Ningún repositorio coincide."


def _commits(a):
    a2 = dict(a)
    with db.session() as conn:
        if not db.find_repo(conn, a["repositorio"]):
            raise ValueError(f"No encontré el repositorio «{a['repositorio']}».")
        data = summary.query(conn, _f(a2))
    if not data["repos"]:
        return "Sin commits con esos filtros."
    r = data["repos"][0]
    lines = [f"{r['full_name']}: {r['total']} commits"]
    for m in r["months"]:
        lines.append(f"\n{m['label']} ({m['count']})")
        for c in m["commits"]:
            lines.append(f"{c['date']} {c['time']} {c['sha']} {c['author']}{' (bot)' if c['bot'] else ''}: {c['message']}")
    return "\n".join(lines)


def _opciones(_a):
    with db.session() as conn:
        cl = conn.execute("select client, count(*) n from bitacora.repo_clients group by client order by n desc").fetchall()
        tg = conn.execute("select tag, count(*) n from bitacora.repo_tags group by tag order by n desc").fetchall()
        none = conn.execute("select count(*) n from bitacora.repos r where not gone and exists "
                            "(select 1 from bitacora.commits c where c.repo_id=r.id) and not exists "
                            "(select 1 from bitacora.repo_clients x where x.repo_id=r.id)").fetchone()["n"]
        pr = conn.execute(
            "select rc.client, rc.project, string_agg(r.name, ', ' order by r.name) repos from bitacora.repo_clients rc "
            "join bitacora.repos r on r.id = rc.repo_id where rc.project is not null and not r.gone "
            "group by rc.client, rc.project order by rc.client, rc.project").fetchall()
        sinp = conn.execute(
            "select rc.client, count(*) n from bitacora.repo_clients rc join bitacora.repos r on r.id = rc.repo_id "
            "where rc.project is null and not r.gone and exists (select 1 from bitacora.commits c where c.repo_id=r.id) "
            "group by rc.client").fetchall()
    sin = {r["client"]: r["n"] for r in sinp}
    lines = ["Clientes: " + ", ".join(f"{r['client']} ({r['n']} repos)" for r in cl),
             f"Repos con commits y sin cliente: {none}", "", "Proyectos por cliente:"]
    for c in [r["client"] for r in cl]:
        ps = [r for r in pr if r["client"] == c]
        lines.append(f"- {c}: " + ("; ".join(f"{r['project']} ({r['repos']})" for r in ps) or "sin proyectos")
                     + (f" · {sin[c]} repos sin proyecto" if sin.get(c) else ""))
    lines += ["", "Etiquetas: " + ", ".join(f"{r['tag']} ({r['n']})" for r in tg)]
    return "\n".join(lines)


def _asignar(a):
    modo = a.get("modo", "agregar")
    res = []
    with db.session() as conn:
        meta = db.get_meta(conn)
        for name in a["repositorios"]:
            r = db.find_repo(conn, name)
            if not r:
                res.append(f"- {name}: no encontrado")
                continue
            cur = meta.get(r["id"], {"clients": [], "tags": [], "projects": {}})
            cl, tg = cur["clients"], cur["tags"]
            new_cl, new_tg = a.get("clientes"), a.get("etiquetas")
            if modo == "reemplazar":
                cl = new_cl if new_cl is not None else cl
                tg = new_tg if new_tg is not None else tg
            elif modo == "quitar":
                cl = [x for x in cl if x.lower() not in [y.lower() for y in new_cl or []]]
                tg = [x for x in tg if x not in [y.lower() for y in new_tg or []]]
            else:
                cl = cl + list(new_cl or [])
                tg = tg + list(new_tg or [])
            out = db.set_meta(conn, r["id"], cl, tg)
            res.append(f"- {r['full_name']}: clientes {', '.join(out['clients']) or '—'}; "
                       f"etiquetas {', '.join(out['tags']) or '—'}")
    return "Actualizado:\n" + "\n".join(res)


def _proyecto(a):
    client = (a.get("cliente") or "").strip()
    project = (a.get("proyecto") or "").strip()
    if not client:
        raise ValueError("Falta el cliente.")
    res = []
    with db.session() as conn:
        meta = db.get_meta(conn)
        for name in a["repositorios"]:
            r = db.find_repo(conn, name)
            if not r:
                res.append(f"- {name}: no encontrado")
                continue
            cur = meta.get(r["id"], {"clients": [], "tags": [], "projects": {}})
            match = next((c for c in cur["clients"] if c.lower() == client.lower()), None)
            if not match:
                projects = dict(cur["projects"]); projects[client] = project
                db.set_meta(conn, r["id"], cur["clients"] + [client], cur["tags"], projects)
                res.append(f"- {r['full_name']}: cliente {client} agregado" + (f", proyecto «{project}»" if project else ""))
            else:
                db.set_project(conn, r["id"], match, project)
                res.append(f"- {r['full_name']}: " + (f"proyecto «{project}» para {match}" if project
                                                       else f"sin proyecto para {match}"))
    return "Actualizado:\n" + "\n".join(res)


def _sync(a):
    st = sync.run(force=bool(a.get("forzar")), budget=min(settings.sync_budget, 42))
    last = st.get("last") or {}
    msg = (f"Repos en GitHub: {last.get('repos_total')}. Repos nuevos en esta revisión: "
           f"{', '.join(n.split('/')[-1] for n in last.get('new_repos', [])) or 'ninguno'}. "
           f"Repos revisados: {last.get('repos_fetched')}. Commits nuevos: {last.get('new_commits')}.")
    if last.get("errors"):
        msg += f" Errores: {'; '.join(last['errors'][:5])}"
    msg += (f" Faltan {st['pending']} repos: vuelve a llamar sincronizar_github para continuar."
            if st["pending"] else " Revisión completa.")
    return msg


HANDLERS = {"resumen_commits": _resumen, "matriz_mensual": _matriz, "listar_repositorios": _listar,
            "commits_de_repositorio": _commits, "clientes_y_etiquetas": _opciones,
            "asignar_clientes_etiquetas": _asignar, "asignar_proyecto": _proyecto, "sincronizar_github": _sync}


def handle(msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method", "")
    if mid is None:  # notificación (ej. notifications/initialized)
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        req = (msg.get("params") or {}).get("protocolVersion")
        return ok({"protocolVersion": req if req in PROTOCOLS else PROTOCOLS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "bitacora-commits", "version": "1.0.0"},
                   "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        p = msg.get("params") or {}
        fn = HANDLERS.get(p.get("name"))
        if not fn:
            return err(-32602, f"Herramienta desconocida: {p.get('name')}")
        try:
            text = fn(p.get("arguments") or {})
            return ok({"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:  # noqa: BLE001
            return ok({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
    if method in ("resources/list", "prompts/list"):
        return ok({method.split("/")[0]: []})
    return err(-32601, f"Método no soportado: {method}")


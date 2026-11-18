"""Resumen de commits por proyecto, repositorio y mes, con SHAs, en JSON, Markdown o CSV.

Cuando se filtra por cliente, los repositorios se agrupan por el proyecto con que ese cliente los
reconoce (ej. «Generador de sitios web» agrupa backend_ y frontend_website_cms_generator).
"""
import csv
import io
from dataclasses import asdict, dataclass

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
SIN_PROYECTO = "Sin proyecto asignado"


def mes_largo(ym: str) -> str:
    y, m = ym.split("-")
    return f"{MESES[int(m) - 1]} {y}"


@dataclass
class Filters:
    month_from: str | None = None   # YYYY-MM
    month_to: str | None = None     # YYYY-MM
    client: str | None = None       # "__none" = sin cliente
    tag: str | None = None          # "__none" = sin etiqueta
    repo: str | None = None         # nombre o owner/nombre
    bots: bool = False
    project: str | None = None      # requiere client; "__none" = sin proyecto

    @property
    def by_client(self) -> bool:
        return bool(self.client) and self.client != "__none"


def _where(f: Filters) -> tuple[str, list]:
    where, args = ["not r.gone"], []
    if f.month_from:
        where.append("c.month >= %s"); args.append(f.month_from)
    if f.month_to:
        where.append("c.month <= %s"); args.append(f.month_to)
    if not f.bots:
        where.append("not c.is_bot")
    if f.repo:
        where.append("(lower(r.name) = lower(%s) or lower(r.full_name) = lower(%s))"); args += [f.repo, f.repo]
    if f.client == "__none":
        where.append("not exists (select 1 from bitacora.repo_clients rc where rc.repo_id = r.id)")
    elif f.client:
        cond = "rc.repo_id = r.id and lower(rc.client) = lower(%s)"
        args.append(f.client)
        if f.project == "__none":
            cond += " and rc.project is null"
        elif f.project:
            cond += " and lower(rc.project) = lower(%s)"
            args.append(f.project)
        where.append(f"exists (select 1 from bitacora.repo_clients rc where {cond})")
    if f.tag == "__none":
        where.append("not exists (select 1 from bitacora.repo_tags rt where rt.repo_id = r.id)")
    elif f.tag:
        where.append("exists (select 1 from bitacora.repo_tags rt where rt.repo_id = r.id and rt.tag = %s)")
        args.append(f.tag.lower())
    return " and ".join(where), args


def _project_for(meta: dict, client: str | None) -> str | None:
    if not client:
        return None
    for c, p in meta.get("projects", {}).items():
        if c.lower() == client.lower():
            return p
    return None


def query(conn, f: Filters) -> dict:
    from .db import get_meta
    where, args = _where(f)
    rows = conn.execute(
        f"""select r.id as repo_id, r.name, r.full_name, r.html_url, c.month, c.sha, c.local_date,
                   c.local_time, c.author_name, c.is_bot, c.message
            from bitacora.commits c join bitacora.repos r on r.id = c.repo_id
            where {where}
            order by lower(r.name), c.month, c.local_date, c.local_time""", args).fetchall()
    meta = get_meta(conn)
    repos: dict[int, dict] = {}
    for row in rows:
        m = meta.get(row["repo_id"], {"clients": [], "tags": [], "projects": {}})
        rep = repos.setdefault(row["repo_id"], {
            "repo": row["name"], "full_name": row["full_name"], "url": row["html_url"],
            "clients": m["clients"], "tags": m["tags"], "projects": m["projects"],
            "project": _project_for(m, f.client if f.by_client else None),
            "total": 0, "months": {}})
        mon = rep["months"].setdefault(row["month"], {"month": row["month"], "label": mes_largo(row["month"]),
                                                      "count": 0, "commits": []})
        mon["count"] += 1
        rep["total"] += 1
        mon["commits"].append({"sha": row["sha"], "short": row["sha"][:7], "date": row["local_date"].isoformat(),
                               "time": row["local_time"], "author": row["author_name"], "bot": row["is_bot"],
                               "message": row["message"], "repo": row["name"],
                               "url": f"{row['html_url']}/commit/{row['sha']}" if row["html_url"] else None})
    out = []
    for rep in repos.values():
        rep["months"] = sorted(rep["months"].values(), key=lambda m: m["month"])
        out.append(rep)
    by_month: dict[str, int] = {}
    for rep in out:
        for m in rep["months"]:
            by_month[m["month"]] = by_month.get(m["month"], 0) + m["count"]

    # Agrupación por proyecto del cliente filtrado.
    groups = []
    if f.by_client:
        g: dict[str, dict] = {}
        for rep in out:
            name = rep["project"] or SIN_PROYECTO
            grp = g.setdefault(name, {"project": name, "assigned": bool(rep["project"]), "repos": [],
                                      "total": 0, "months": {}})
            grp["repos"].append(rep["full_name"])
            grp["total"] += rep["total"]
            for m in rep["months"]:
                gm = grp["months"].setdefault(m["month"], {"month": m["month"], "label": m["label"],
                                                           "count": 0, "commits": []})
                gm["count"] += m["count"]
                gm["commits"].extend(m["commits"])
        for grp in g.values():
            grp["months"] = sorted(grp["months"].values(), key=lambda m: m["month"])
            for m in grp["months"]:
                m["commits"].sort(key=lambda c: (c["date"], c["time"], c["repo"]))
            grp["first"] = grp["months"][0]["month"]
            grp["last"] = grp["months"][-1]["month"]
            groups.append(grp)
        groups.sort(key=lambda x: (not x["assigned"], -x["total"]))
    return {"filters": asdict(f), "repos": out, "projects": groups,
            "total": sum(r["total"] for r in out), "by_month": dict(sorted(by_month.items()))}


def _scope(f: dict) -> str:
    parts = []
    if f.get("month_from") or f.get("month_to"):
        a = mes_largo(f["month_from"]) if f.get("month_from") else "el inicio"
        b = mes_largo(f["month_to"]) if f.get("month_to") else "la fecha"
        parts.append(a if a == b else f"de {a} a {b}")
    if f.get("client"):
        parts.append("sin cliente asignado" if f["client"] == "__none" else f"cliente {f['client']}")
    if f.get("project"):
        parts.append("sin proyecto asignado" if f["project"] == "__none" else f"proyecto «{f['project']}»")
    if f.get("tag"):
        parts.append("sin etiqueta" if f["tag"] == "__none" else f"etiqueta «{f['tag']}»")
    if f.get("repo"):
        parts.append(f"repositorio {f['repo']}")
    if not f.get("bots"):
        parts.append("sin commits automáticos de bots")
    return "; ".join(parts)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def to_markdown(data: dict, with_messages: bool = True) -> str:
    f = data["filters"]
    lines = ["# Resumen de commits", ""]
    scope = _scope(f)
    if scope:
        lines += [f"Alcance: {scope}.", ""]
    lines += [f"Total: {_plural(data['total'], 'commit')} en {_plural(len(data['repos']), 'repositorio')}"
              + (f" y {_plural(len(data['projects']), 'proyecto')}." if data["projects"] else "."), ""]
    if data["projects"]:
        for g in data["projects"]:
            lines.append(f"## {g['project']} ({_plural(g['total'], 'commit')})")
            lines.append("Repositorios: " + ", ".join(g["repos"]))
            lines.append("")
            for m in g["months"]:
                lines.append(f"### {m['label']}: {_plural(m['count'], 'commit')}")
                for c in m["commits"]:
                    msg = f" {c['message']}" if with_messages else ""
                    lines.append(f"- `{c['short']}` {c['date']} {c['time']} · {c['repo']}{' ·' if msg else ''}{msg}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"
    for rep in data["repos"]:
        extra = []
        if rep["clients"]:
            extra.append("clientes: " + ", ".join(
                f"{c} ({rep['projects'][c]})" if c in rep["projects"] else c for c in rep["clients"]))
        if rep["tags"]:
            extra.append("etiquetas: " + ", ".join(rep["tags"]))
        lines.append(f"## {rep['full_name']} ({_plural(rep['total'], 'commit')})")
        if extra:
            lines.append("_" + "; ".join(extra) + "_")
        lines.append("")
        for m in rep["months"]:
            lines.append(f"- **{m['label']}**: {_plural(m['count'], 'commit')}")
            for c in m["commits"]:
                msg = f" {c['message']}" if with_messages else ""
                lines.append(f"  - `{c['short']}` {c['date']} {c['time']}{msg}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_csv(data: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["proyecto", "repositorio", "clientes", "etiquetas", "mes", "fecha", "hora", "sha", "autor", "bot",
                "mensaje"])
    for rep in data["repos"]:
        proj = rep["project"] or ("; ".join(f"{c}: {p}" for c, p in rep["projects"].items())
                                  if not data["filters"].get("client") else "")
        for m in rep["months"]:
            for c in m["commits"]:
                w.writerow([proj, rep["full_name"], "; ".join(rep["clients"]), "; ".join(rep["tags"]), m["month"],
                            c["date"], c["time"], c["sha"], c["author"], "sí" if c["bot"] else "no", c["message"]])
    return buf.getvalue()

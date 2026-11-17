"""Uso desde terminal:

    docker compose exec bitacora python -m app.cli sync [--force]
    docker compose exec bitacora python -m app.cli summary --from 2026-05 --to 2026-05 [--client Hulempak] [--tag seo]
    docker compose exec bitacora python -m app.cli import-meta seed/meta.json [--overwrite]
    docker compose exec bitacora python -m app.cli export-meta > meta.json
    docker compose exec bitacora python -m app.cli backup /data/backup.sqlite3
"""
import argparse
import json
import sqlite3
import sys

from . import db, summary
from .sync import manager


def main(argv=None):
    p = argparse.ArgumentParser(prog="bitacora")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="Busca repos nuevos y descarga commits nuevos")
    s.add_argument("--force", action="store_true", help="Vuelve a revisar todos los repos")
    r = sub.add_parser("summary", help="Resumen por repo y mes con SHAs")
    r.add_argument("--from", dest="month_from")
    r.add_argument("--to", dest="month_to")
    r.add_argument("--client")
    r.add_argument("--tag")
    r.add_argument("--repo")
    r.add_argument("--bots", action="store_true")
    r.add_argument("--format", choices=["md", "csv", "json"], default="md")
    r.add_argument("--no-messages", action="store_true")
    i = sub.add_parser("import-meta", help="Importa clientes y etiquetas desde JSON")
    i.add_argument("path")
    i.add_argument("--overwrite", action="store_true")
    sub.add_parser("export-meta", help="Exporta clientes y etiquetas a JSON")
    b = sub.add_parser("backup", help="Copia consistente de la base de datos")
    b.add_argument("path", nargs="?", default="/data/backup.sqlite3")
    a = p.parse_args(argv)

    db.init()
    if a.cmd == "sync":
        res = manager.run_blocking(force=a.force)
        print(f"Repos: {res['repos_total']} · nuevos: {len(res['new_repos'])} · revisados: {res['repos_fetched']}"
              f" · commits nuevos: {res['new_commits']}")
        for n in res["new_repos"]:
            print(f"  + {n}")
        for e in res["errors"]:
            print(f"  ! {e}", file=sys.stderr)
    elif a.cmd == "summary":
        f = summary.Filters(a.month_from, a.month_to, a.client, a.tag, a.repo, a.bots)
        with db.session() as conn:
            data = summary.query(conn, f)
        if a.format == "json":
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif a.format == "csv":
            sys.stdout.write(summary.to_csv(data))
        else:
            sys.stdout.write(summary.to_markdown(data, not a.no_messages))
    elif a.cmd == "import-meta":
        with open(a.path) as fh, db.session() as conn:
            print(db.import_meta(conn, json.load(fh), overwrite=a.overwrite))
    elif a.cmd == "export-meta":
        with db.session() as conn:
            print(json.dumps(db.export_meta(conn), ensure_ascii=False, indent=2))
    elif a.cmd == "backup":
        src = db.connect()
        dst = sqlite3.connect(a.path)
        src.backup(dst)
        dst.close(); src.close()
        print(f"Respaldo escrito en {a.path}")


if __name__ == "__main__":
    main()

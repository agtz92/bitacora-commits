"""Uso desde terminal (con DATABASE_URL y GITHUB_TOKEN en el entorno):

    python -m app.cli sync [--force]
    python -m app.cli summary --from 2026-05 --to 2026-05 [--client Hulempak] [--tag seo] [--format md|csv|json]
    python -m app.cli import-meta meta.json
    python -m app.cli export-meta > meta.json
"""
import argparse
import json
import sys

from . import db, summary
from . import sync


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
    sub.add_parser("export-meta", help="Exporta clientes y etiquetas a JSON")
    a = p.parse_args(argv)

    if a.cmd == "sync":
        st = sync.start(force=a.force)
        print(f"Repos: {st['repos_total']} · nuevos: {len(st['new_repos'])} · por revisar: {st['pending']}")
        while True:
            st = sync.step(budget=600)
            print(f"  faltan {st['pending']}")
            if not st["running"]:
                break
        last = st["last"] or {}
        print(f"Commits nuevos: {last.get('new_commits')}")
        for e in last.get("errors", []):
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
            print(db.import_meta(conn, json.load(fh)))
    elif a.cmd == "export-meta":
        with db.session() as conn:
            print(json.dumps(db.export_meta(conn), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

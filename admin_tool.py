"""Moderation per Kommandozeile (es gibt bewusst keine Admin-Seite im Web).

Nutzung (lokal, gegen die EXTERNE Datenbank-URL aus Render):
  DATABASE_URL="postgresql://..." python admin_tool.py reports
  DATABASE_URL="postgresql://..." python admin_tool.py ban 12
  DATABASE_URL="postgresql://..." python admin_tool.py unban 12
  DATABASE_URL="postgresql://..." python admin_tool.py handled 3
"""
import sys

from app import create_app
from extensions import db
from models import Report, User

app = create_app()


def main(argv):
    if len(argv) < 2 or argv[1] not in {"reports", "ban", "unban", "handled"}:
        print(__doc__)
        return 1
    with app.app_context():
        cmd = argv[1]
        if cmd == "reports":
            rows = Report.query.filter_by(handled=False).order_by(Report.created_at).all()
            if not rows:
                print("Keine offenen Meldungen.")
            for r in rows:
                print(f"#{r.id} {r.created_at:%d.%m.%Y %H:%M}  {r.reporter.name} (ID {r.reporter_id}) "
                      f"meldet {r.reported.name} (ID {r.reported_id}, {r.reported.email}, "
                      f"gesperrt={r.reported.is_banned})\n    Grund: {r.reason}  {r.details or ''}")
            return 0
        if len(argv) < 3 or not argv[2].isdigit():
            print("ID fehlt.")
            return 1
        ident = int(argv[2])
        if cmd in ("ban", "unban"):
            user = db.session.get(User, ident)
            if not user:
                print("Nutzer nicht gefunden.")
                return 1
            user.is_banned = cmd == "ban"
            db.session.commit()
            print(f"{user.name} ({user.email}): {'gesperrt' if user.is_banned else 'entsperrt'}.")
        else:
            rep = db.session.get(Report, ident)
            if not rep:
                print("Meldung nicht gefunden.")
                return 1
            rep.handled = True
            db.session.commit()
            print("Meldung als erledigt markiert.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

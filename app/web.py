"""Template rendering helpers shared by all routers."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .listings import STATUS_LABELS, VERIFICATION_LABELS
from .security import csrf_token

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

NOT_SPECIFIED = "Nicht angegeben"


def fmt_date(value) -> str:
    if value is None:
        return NOT_SPECIFIED
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)


def or_missing(value, text: str = NOT_SPECIFIED):
    if value is None or value == "" or value == []:
        return Markup(f'<span class="missing">{escape(text)}</span>')
    return value


def yes_no(value) -> Markup | str:
    if value is None:
        return or_missing(None, "Bitte beim Betrieb nachfragen")
    return "Ja" if value else "Nein"


def nl2br(value: str | None) -> Markup:
    if not value:
        return Markup("")
    return Markup("<br>").join(escape(line) for line in value.splitlines())


templates.env.filters.update(fmt_date=fmt_date, or_missing=or_missing, yes_no=yes_no, nl2br=nl2br)
templates.env.globals.update(STATUS_LABELS=STATUS_LABELS, VERIFICATION_LABELS=VERIFICATION_LABELS,
                             NOT_SPECIFIED=NOT_SPECIFIED)

TRAVEL_MODES = [("oepnv", "Bus & Bahn"), ("fahrrad", "Fahrrad"), ("zu_fuss", "Zu Fuß"),
                ("auto", "Ich werde gebracht (Auto)")]
SCHOOL_TYPES = ["Gymnasium", "Realschule", "Hauptschule", "Gesamtschule", "Integrierte Gesamtschule",
                "Förderschule", "Mittelstufenschule", "Realschule plus", "Sonstige"]
EMPLOYER_TYPES = ["Unternehmen", "Krankenhaus", "Arztpraxis", "Behörde", "Handwerksbetrieb", "Kfz-Betrieb",
                  "Kita", "Labor", "Verein / gemeinnützig", "Sonstige"]
templates.env.globals.update(TRAVEL_MODES=TRAVEL_MODES, SCHOOL_TYPES=SCHOOL_TYPES, EMPLOYER_TYPES=EMPLOYER_TYPES)


def render(request: Request, name: str, status_code: int = 200, **ctx):
    ctx.setdefault("admin_area", request.url.path.startswith("/admin"))
    ctx["request"] = request
    ctx["csrf_token"] = csrf_token(request)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)

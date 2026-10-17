"""Rule-based reading of German listing text: age, grade, practicum dates, deadline, email, documents.

Used for imported listings, which often state these only in free text. The results are suggestions:
they only fill empty fields and the listing stays in the review queue with a note to check them.
Nothing is guessed: a value is only returned when the text states it in a recognisable form.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

AGE_RANGE = (12, 21)
GRADE_RANGE = (5, 13)
MAX_WINDOW_DAYS = 70

MONTHS = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3, "april": 4, "apr": 4,
    "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
    "sept": 9, "oktober": 10, "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
# 02.02.2027 | 2.2.27 | 02.02. | 2. Februar 2027 | 2. Feb.
DATE = rf"(\d{{1,2}})\.\s*(?:(\d{{1,2}})\.|({_MONTH_RE})\.?)\s*(\d{{4}}|\d{{2}}(?!\d))?"
_DATE_GROUPS = 4
TO = r"\s*(?:-|bis(?:\s+(?:zum|einschließlich))?)\s*"

GRADE_WORD = r"(?:klassen?(?:stufen?)?|jahrgangsstufen?|jahrgangs?|jg\.|kl\.)"

DOCUMENTS = [
    (r"\b(?:anschreiben|motivationsschreiben|bewerbungsschreiben)\b", "Anschreiben"),
    (r"\blebenslauf", "Lebenslauf"),
    (r"\bzeugnis", "Letztes Zeugnis"),
    (r"\b(?:schulbescheinigung|bescheinigung\s+(?:der|deiner|ihrer)\s+schule|praktikumsvereinbarung|vereinbarung\s+mit\s+der\s+schule)",
     "Bescheinigung oder Vereinbarung der Schule"),
    (r"\bmasern", "Nachweis Masernschutz"),
    (r"\b(?:gesundheitszeugnis|belehrung\s+nach\s+§\s*43|infektionsschutz)", "Belehrung nach Infektionsschutzgesetz"),
]
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.I)


@dataclass
class Extracted:
    min_age: int | None = None
    max_age: int | None = None
    min_grade: int | None = None
    max_grade: int | None = None
    windows: list[tuple[date, date]] = field(default_factory=list)
    deadline: date | None = None
    emails: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)

    def summary(self) -> list[str]:
        """German one-liners for what was found, for the admin's data-quality note."""
        out = []
        if self.min_age or self.max_age:
            out.append("Alter " + _span(self.min_age, self.max_age, "Jahre"))
        if self.min_grade or self.max_grade:
            out.append("Klasse " + _span(self.min_grade, self.max_grade, ""))
        if self.windows:
            out.append("Termine " + ", ".join(f"{a:%d.%m.%Y}–{b:%d.%m.%Y}" for a, b in self.windows))
        if self.deadline:
            out.append(f"Bewerbungsfrist {self.deadline:%d.%m.%Y}")
        if self.emails:
            out.append("E-Mail " + self.emails[0])
        if self.documents:
            out.append("Unterlagen: " + ", ".join(self.documents))
        return out


def _span(lo: int | None, hi: int | None, unit: str) -> str:
    text = f"{lo}–{hi}" if lo and hi and lo != hi else (f"ab {lo}" if lo and not hi else (f"bis {hi}" if hi and not lo else str(lo)))
    return f"{text} {unit}".strip()


def _normalise(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", text).lower()


def _year(raw: str | None) -> int | None:
    if not raw:
        return None
    y = int(raw)
    return y + 2000 if y < 100 else y


def _date(groups: tuple, today: date, year_hint: int | None = None) -> date | None:
    day, month_num, month_name, year = groups
    month = int(month_num) if month_num else MONTHS.get(month_name)
    y = _year(year) or year_hint
    try:
        if y is None:
            d = date(today.year, month, int(day))
            # A date without a year that lies well in the past means next year.
            return date(today.year + 1, month, int(day)) if (today - d).days > 60 else d
        d = date(y, month, int(day))
    except (TypeError, ValueError):
        return None
    return d if today.year - 1 <= d.year <= today.year + 3 else None


def _in(value: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= value <= bounds[1]


def _ages(t: str) -> tuple[int | None, int | None]:
    lo: list[int] = []
    hi: list[int] = []
    for m in re.finditer(r"\b(\d{2})\s*(?:-|bis)\s*(\d{2})\s*jahr", t):
        a, b = int(m[1]), int(m[2])
        if _in(a, AGE_RANGE) and _in(b, AGE_RANGE) and a <= b:
            lo.append(a)
            hi.append(b)
    for m in re.finditer(r"\b(?:ab|mindestens|mind\.|nicht\s+jünger\s+als)\s*(\d{2})\s*(?:jahr|j\.)|\bmindestalter\s*:?\s*(\d{2})\b", t):
        if _in(int(m[1] or m[2]), AGE_RANGE):
            lo.append(int(m[1] or m[2]))
    for m in re.finditer(r"\b(?:höchstens|maximal|max\.|bis)\s*(\d{2})\s*jahr", t):
        if _in(int(m[1]), AGE_RANGE) and not re.search(r"\d{2}\s*$", t[max(0, m.start() - 8):m.start()]):
            hi.append(int(m[1]))
    return (min(lo) if lo else None, max(hi) if hi else None)


def _grades(t: str) -> tuple[int | None, int | None]:
    lo: list[int] = []
    hi: list[int] = []
    open_ended = False
    spans = []

    def add(a: int, b: int, m: re.Match) -> None:
        if _in(a, GRADE_RANGE) and _in(b, GRADE_RANGE) and a <= b:
            lo.append(a)
            hi.append(b)
            spans.append(m.span())

    def seen(m: re.Match) -> bool:
        return any(s <= m.start() < e for s, e in spans)

    # Klassen 8 bis 10 | Jahrgangsstufe 9-10
    for m in re.finditer(rf"\b{GRADE_WORD}\s*(\d{{1,2}})\.?\s*(?:-|bis|und|/)\s*(\d{{1,2}})\b", t):
        add(int(m[1]), int(m[2]), m)
    # 9./10. Klasse | 8. bis 10. Klassen | 9. und 10. Jahrgangsstufe
    for m in re.finditer(rf"\b(\d{{1,2}})\.?\s*(?:-|bis|und|/)\s*(\d{{1,2}})\.\s*{GRADE_WORD}", t):
        if not seen(m):
            add(int(m[1]), int(m[2]), m)
    # ab Klasse 8 | ab der 8. Klasse | ab Jahrgangsstufe 9
    for m in re.finditer(rf"\bab\s+(?:der\s+)?(?:{GRADE_WORD}\s*(\d{{1,2}})\b|(\d{{1,2}})\.\s*{GRADE_WORD})", t):
        g = int(m[1] or m[2])
        if _in(g, GRADE_RANGE) and not seen(m):
            lo.append(g)
            open_ended = True
            spans.append(m.span())
    # 8. Klasse | Klasse 9 | Jahrgangsstufe 9
    for m in re.finditer(rf"\b(?:(\d{{1,2}})\.\s*{GRADE_WORD}|{GRADE_WORD}\s*(\d{{1,2}})\b)", t):
        if seen(m):
            continue
        g = int(m[1] or m[2])
        if _in(g, GRADE_RANGE):
            lo.append(g)
            hi.append(g)
    if not lo:
        return None, None
    return min(lo), (None if open_ended or not hi else max(hi))


def _windows(t: str, today: date) -> list[tuple[date, date]]:
    out = []
    for m in re.finditer(DATE + TO + DATE, t):
        g = m.groups()
        start_g, end_g = g[:_DATE_GROUPS], g[_DATE_GROUPS:]
        before = t[max(0, m.start() - 40):m.start()]
        if re.search(r"bewerb|frist|einsende", before):
            continue
        end = _date(end_g, today)
        if end is None:
            continue
        start = _date(start_g, today, year_hint=end.year)
        if start and start > end and _year(start_g[3]) is None:
            start = _date(start_g, today, year_hint=end.year - 1)
        if start and start <= end and (end - start).days <= MAX_WINDOW_DAYS and (start, end) not in out:
            out.append((start, end))
    return out


def _deadline(t: str, today: date) -> date | None:
    patterns = [
        rf"(?:bewerbungsschluss|bewerbungsfrist|einsendeschluss|bewerbungsende)\s*(?:ist|endet)?\s*(?:am|zum|der)?\s*:?\s*(?:spätestens\s+)?(?:\w+,\s*)?(?:den\s+)?{DATE}",
        rf"(?:bewerbung(?:en)?|bewirb\s+dich|bewerben\s+sie\s+sich|bewerben)\s+(?:\w+\s+){{0,4}}?bis\s+(?:zum\s+|spätestens\s+(?:zum\s+|am\s+)?)?(?:\w+,\s*)?(?:den\s+)?{DATE}",
        rf"bis\s+(?:zum\s+|spätestens\s+(?:zum\s+|am\s+)?)?{DATE}[^.]{{0,40}}?bewerb",
    ]
    for p in patterns:
        m = re.search(p, t)
        if m:
            g = m.groups()[-_DATE_GROUPS:]
            d = _date(g, today)
            if d:
                return d
    return None


def extract(text: str | None, today: date | None = None) -> Extracted:
    today = today or date.today()
    if not text:
        return Extracted()
    t = _normalise(text)
    res = Extracted()
    res.min_age, res.max_age = _ages(t)
    res.min_grade, res.max_grade = _grades(t)
    res.windows = _windows(t, today)
    res.deadline = _deadline(t, today)
    for m in EMAIL.finditer(text):
        email = m[0].rstrip(".").lower()
        if email not in res.emails and not email.startswith(("noreply", "no-reply")):
            res.emails.append(email)
    res.documents = [name for pattern, name in DOCUMENTS if re.search(pattern, t)]
    return res

import argparse
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen


STATE_PATH = Path(__file__).with_name("seen_matches.json")
MINIMUM_LEVEL = Decimal("3.50")
MAXIMUM_LEVEL = Decimal("4.00")
EARLIEST_START = time(20, 0)
CHECK_START_HOUR = 9
CHECK_END_HOUR = 23
STATE_RETENTION_DAYS = 60

LEVEL_PATTERN = re.compile(r"^(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?)$")
MATCH_ID_PATTERN = re.compile(r"/Matches/Match\.aspx\?id=([A-Za-z0-9]+)", re.I)
SPOTS_PATTERN = re.compile(r"(\d+)\s+spots?\s+left", re.I)


@dataclass(frozen=True)
class Match:
    match_id: str
    starts_at: datetime
    minimum_level: Decimal
    maximum_level: Decimal
    court: str
    spots_left: int


class Element:
    BLOCK_TAGS = {
        "article", "div", "footer", "header", "li", "p", "section", "td", "tr",
    }

    def __init__(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        self.tag = tag
        self.attributes = dict(attributes)
        self.children: list[Element | str] = []

    def descendants(self):
        for child in self.children:
            if isinstance(child, Element):
                yield child
                yield from child.descendants()

    def find_class(self, class_name: str):
        for element in self.descendants():
            if class_name in element.attributes.get("class", "").split():
                yield element

    def text_content(self, excluded_classes: set[str] | None = None) -> str:
        parts: list[str] = []
        excluded_classes = excluded_classes or set()

        def append_text(element: Element) -> None:
            if excluded_classes.intersection(element.attributes.get("class", "").split()):
                return
            for child in element.children:
                if isinstance(child, str):
                    parts.append(child)
                else:
                    is_block = child.tag in self.BLOCK_TAGS
                    if is_block:
                        parts.append("\n")
                    append_text(child)
                    if is_block:
                        parts.append("\n")

        append_text(self)
        return "".join(parts)


class MatchHTMLParser(HTMLParser):
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("document", [])
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.stack[-1].children.append("\n")
            return
        element = Element(tag, attrs)
        self.stack[-1].children.append(element)
        if tag not in self.VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(Element(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def get_required_https_url(secret_name: str) -> str:
    value = os.environ.get(secret_name)
    if not value:
        raise RuntimeError(f"Set the {secret_name} GitHub Actions secret.")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise RuntimeError(f"The {secret_name} secret must be an HTTPS URL without a query or fragment.")
    return value


def build_search_url(today: date, site_url: str) -> str:
    parameters = {
        "sexo": "todos",
        "amigos": "false",
        "jugado": "false",
        "idDeporte": "2",
        "nivel": "false",
        "nivelMinimo": "3,50",
        "nivelMaximo": "4,00",
        "fechaDesde": today.strftime("%d/%m/%Y"),
        "fechaHasta": "",
        "horaDesde": "20:00",
        "horaHasta": "",
        "idcentro": "",
    }
    return f"{site_url}?{urlencode(parameters)}"


def parse_matches(html: str) -> list[Match]:
    parser = MatchHTMLParser()
    parser.feed(html)
    cards = [
        element
        for element in parser.root.descendants()
        if {"contenedorContenidoPartidas3", "mrp"}.issubset(
            element.attributes.get("class", "").split()
        )
    ]

    if not cards and "open matches" not in html.lower():
        raise ValueError("The matches page did not contain the expected page or match-card markup.")

    matches: list[Match] = []
    for card in cards:
        onclick = card.attributes.get("onclick", "")
        match_id = MATCH_ID_PATTERN.search(onclick)
        date_container = next(card.find_class("divCalendar"), None)
        if match_id is None or date_container is None:
            raise ValueError("A match card is missing its match ID or date.")

        date_parts = [
            span.text_content().strip()
            for span in date_container.descendants()
            if span.tag == "span" and span.text_content().strip()
        ]
        if len(date_parts) != 3:
            raise ValueError("A match card has an unrecognized date.")

        try:
            match_date = datetime.strptime(
                f"{date_parts[0]} {date_parts[1]} {date_parts[2]}",
                "%b %d %Y",
            ).date()
        except ValueError as error:
            raise ValueError(f"A match card has an invalid date: {date_parts!r}") from error

        lines = [
            line.strip()
            for line in card.text_content({"divCalendar"}).splitlines()
            if line.strip()
        ]
        start_time_match = next(
            (match for line in lines if (match := re.match(r"^(\d{1,2}:\d{2})(?:\s|$)", line))),
            None,
        )
        level_match = next(
            (match for line in lines if (match := LEVEL_PATTERN.fullmatch(line))),
            None,
        )
        court = next((line for line in lines if line.lower().startswith("court ")), None)
        spots_match = next(
            (match for line in lines if (match := SPOTS_PATTERN.search(line))),
            None,
        )

        if start_time_match is None or level_match is None or court is None or spots_match is None:
            raise ValueError(
                f"Could not read all expected details from match {match_id.group(1)}: {lines!r}"
            )

        matches.append(
            Match(
                match_id=match_id.group(1),
                starts_at=datetime.combine(match_date, time.fromisoformat(start_time_match.group(1))),
                minimum_level=Decimal(level_match.group(1).replace(",", ".")),
                maximum_level=Decimal(level_match.group(2).replace(",", ".")),
                court=court,
                spots_left=int(spots_match.group(1)),
            )
        )

    return matches


def find_matching_matches(matches: list[Match], today: date) -> list[Match]:
    return [
        match
        for match in matches
        if match.starts_at.date() >= today
        and match.starts_at.time() >= EARLIEST_START
        and match.minimum_level == MINIMUM_LEVEL
        and match.maximum_level == MAXIMUM_LEVEL
        and match.spots_left > 0
    ]


def is_checking_hour(current_time: datetime) -> bool:
    return CHECK_START_HOUR <= current_time.hour <= CHECK_END_HOUR


def fetch_matches(today: date, site_url: str) -> list[Match]:
    request = Request(
        build_search_url(today, site_url),
        headers={"User-Agent": "PadelMatchChecker/1.0"},
    )
    with urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", errors="replace")
    return parse_matches(html)


def load_state(path: Path = STATE_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as state_file:
        state = json.load(state_file)
    if not isinstance(state, dict) or any(
        not isinstance(match_id, str) or not isinstance(sent_at, str)
        for match_id, sent_at in state.items()
    ):
        raise ValueError(f"Invalid notification state in {path}.")
    return state


def save_state(state: dict[str, str], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as temporary_file:
        json.dump(state, temporary_file, indent=2, sort_keys=True)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)
    temporary_path.replace(path)


def format_notification(matches: list[Match], match_url: str) -> str:
    entries = [
        (
            f"{match.starts_at:%a %d %b %Y} at {match.starts_at:%H:%M}\n"
            f"Level: {match.minimum_level:.2f}–{match.maximum_level:.2f}\n"
            f"{match.court} · {match.spots_left} spot(s) left\n"
            f"{urljoin(match_url, f'?id={match.match_id}')}"
        )
        for match in matches
    ]
    return "New padel matches (level 3.5–4.0, 20:00 or later):\n\n" + "\n\n".join(entries)


def send_telegram_message(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before sending alerts.")

    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urlencode({"chat_id": chat_id, "text": text}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Telegram returned HTTP {error.code}.") from None
    if not result.get("ok"):
        raise RuntimeError(f"Telegram could not send the notification: {result.get('description', 'unknown error')}")


def run(dry_run: bool = False) -> None:
    current_time = datetime.now()
    if not dry_run and not is_checking_hour(current_time):
        print("Outside the 09:00–23:00 local checking window; skipping site check.")
        return

    today = current_time.date()
    site_url = get_required_https_url("MATCHES_SEARCH_URL")
    match_url = get_required_https_url("MATCH_DETAILS_URL")
    matches = find_matching_matches(fetch_matches(today, site_url), today)
    state = load_state()
    cutoff = today - timedelta(days=STATE_RETENTION_DAYS)
    state = {
        match_id: sent_at
        for match_id, sent_at in state.items()
        if date.fromisoformat(sent_at) >= cutoff
    }
    new_matches = [match for match in matches if match.match_id not in state]

    print(f"Found {len(matches)} matching open match(es); {len(new_matches)} not previously alerted.")
    if dry_run:
        for match in matches:
            print(
                f"{match.starts_at:%Y-%m-%d %H:%M} | "
                f"{match.minimum_level:.2f}-{match.maximum_level:.2f} | "
                f"{match.court} | {match.spots_left} spot(s) | {match.match_id}"
            )
        return

    if new_matches:
        send_telegram_message(format_notification(new_matches, match_url))
        for match in new_matches:
            state[match.match_id] = today.isoformat()

    save_state(state)


def main() -> None:
    argument_parser = argparse.ArgumentParser(description="Check configured open padel matches.")
    argument_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching matches without sending a Telegram message or changing state.",
    )
    arguments = argument_parser.parse_args()

    try:
        run(dry_run=arguments.dry_run)
    except (HTTPError, OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"Match checker failed: {error}") from error


if __name__ == "__main__":
    main()

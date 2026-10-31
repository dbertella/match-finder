import unittest
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from check_matches import (
    Match,
    build_search_url,
    find_matching_matches,
    format_notification,
    get_required_https_url,
    is_checking_hour,
    parse_matches,
)


SAMPLE_HTML = """
<html><body>
  <h1>OPEN MATCHES</h1>
  <div class="contenedorContenidoPartidas3 mrp"
       onclick="window.location='/Matches/Match.aspx?id=abc123'">
    <div class="divCalendar">
      <span>Nov</span><span>4</span><span>2026</span>
    </div>
    <div>
      <div>21:00 Counts for ranking</div>
      <div>Padel</div>
      <div>3.50 - 4.00</div>
      <div>Mixed</div>
      <div>Court 5 (Indoor)</div>
      <div>2 spots left</div>
    </div>
  </div>
</body></html>
"""


class ParseMatchesTests(unittest.TestCase):
    def test_extracts_match_fields(self) -> None:
        matches = parse_matches(SAMPLE_HTML)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].match_id, "abc123")
        self.assertEqual(matches[0].starts_at.isoformat(sep=" "), "2026-11-04 21:00:00")
        self.assertEqual(matches[0].minimum_level, Decimal("3.50"))
        self.assertEqual(matches[0].maximum_level, Decimal("4.00"))
        self.assertEqual(matches[0].court, "Court 5 (Indoor)")
        self.assertEqual(matches[0].spots_left, 2)

    def test_rejects_unexpected_html(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected page"):
            parse_matches("<html><body>Maintenance</body></html>")


class UrlConfigurationTests(unittest.TestCase):
    def test_builds_search_url_using_configured_secret(self) -> None:
        url = build_search_url(
            date(2026, 11, 4),
            "https://example.com/Matches/Search.aspx",
        )

        self.assertEqual(urlsplit(url).netloc, "example.com")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["nivelMinimo"], ["3,50"])
        self.assertEqual(query["nivelMaximo"], ["4,00"])
        self.assertEqual(query["horaDesde"], ["20:00"])

    def test_requires_configured_https_url(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "MATCHES_SEARCH_URL"):
            get_required_https_url("MATCHES_SEARCH_URL")

    def test_notification_uses_configured_match_url(self) -> None:
        match = Match(
            "abc123",
            datetime(2026, 11, 4, 21),
            Decimal("3.50"),
            Decimal("4.00"),
            "Court 5",
            2,
        )

        notification = format_notification(
            [match],
            "https://example.com/Matches/Match.aspx",
        )

        self.assertIn("https://example.com/Matches/Match.aspx?id=abc123", notification)


class CheckingWindowTests(unittest.TestCase):
    def test_includes_hours_from_nine_through_twenty_three(self) -> None:
        self.assertTrue(is_checking_hour(datetime(2026, 11, 4, 9)))
        self.assertTrue(is_checking_hour(datetime(2026, 11, 4, 23)))

    def test_excludes_hours_outside_window(self) -> None:
        self.assertFalse(is_checking_hour(datetime(2026, 11, 4, 8, 59)))
        self.assertFalse(is_checking_hour(datetime(2026, 11, 4, 0)))


class MatchFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matches = [
            Match("exact", datetime(2026, 11, 4, 20), Decimal("3.50"), Decimal("4.00"), "Court 5", 2),
            Match("wrong-level", datetime(2026, 11, 4, 21), Decimal("3.00"), Decimal("3.50"), "Court 2", 2),
            Match("before-time", datetime(2026, 11, 4, 19), Decimal("3.50"), Decimal("4.00"), "Court 3", 1),
            Match("full", datetime(2026, 11, 4, 21), Decimal("3.50"), Decimal("4.00"), "Court 4", 0),
            Match("past", datetime(2026, 11, 3, 21), Decimal("3.50"), Decimal("4.00"), "Court 6", 1),
        ]

    def test_only_includes_future_exact_level_matches_after_20_with_spaces(self) -> None:
        found = find_matching_matches(self.matches, date(2026, 11, 4))

        self.assertEqual([match.match_id for match in found], ["exact"])


if __name__ == "__main__":
    unittest.main()

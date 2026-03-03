"""Tests for the Footballia navigator filtering logic."""

import pytest
from backend.footballia_navigator import FootballiaNavigator


@pytest.fixture
def navigator():
    return FootballiaNavigator()


@pytest.fixture
def sample_team_data():
    """Sample team data structure matching scrape_team_page() output."""
    return {
        "name": "Juventus FC",
        "type": "team",
        "total_matches": 7,
        "seasons": [
            {
                "season": "2024-2025",
                "competitions": [
                    {
                        "name": "Serie A",
                        "matches": [
                            {
                                "date": "September 21, 2024",
                                "home_team": "Juventus FC",
                                "away_team": "SSC Napoli",
                                "stage": "Giornata 5",
                                "match_url": "/matches/juventus-ssc-napoli",
                                "full_url": "https://footballia.eu/matches/juventus-ssc-napoli",
                                "has_video": True,
                                "home_away": "H",
                                "score": "0-1",
                            },
                            {
                                "date": "October 6, 2024",
                                "home_team": "Juventus FC",
                                "away_team": "Cagliari",
                                "stage": "Giornata 7",
                                "match_url": "/matches/juventus-cagliari",
                                "full_url": "https://footballia.eu/matches/juventus-cagliari",
                                "has_video": True,
                                "home_away": "H",
                                "score": "1-1",
                            },
                        ],
                    },
                    {
                        "name": "Champions League",
                        "matches": [
                            {
                                "date": "September 17, 2024",
                                "home_team": "Juventus FC",
                                "away_team": "PSV Eindhoven",
                                "stage": "Group stage",
                                "match_url": "/matches/juventus-psv",
                                "full_url": "https://footballia.eu/matches/juventus-psv",
                                "has_video": True,
                                "home_away": "H",
                                "score": "3-1",
                            },
                        ],
                    },
                    {
                        "name": "Coppa Italia",
                        "matches": [
                            {
                                "date": "December 4, 2024",
                                "home_team": "Juventus FC",
                                "away_team": "Catanzaro",
                                "stage": "Round of 16",
                                "match_url": "/matches/juventus-catanzaro",
                                "full_url": "https://footballia.eu/matches/juventus-catanzaro",
                                "has_video": True,
                                "home_away": "H",
                                "score": "4-0",
                            },
                        ],
                    },
                ],
            },
            {
                "season": "2023-2024",
                "competitions": [
                    {
                        "name": "Serie A",
                        "matches": [
                            {
                                "date": "August 28, 2023",
                                "home_team": "Juventus FC",
                                "away_team": "Bologna FC",
                                "stage": "Giornata 3",
                                "match_url": "/matches/juventus-bologna",
                                "full_url": "https://footballia.eu/matches/juventus-bologna",
                                "has_video": True,
                                "home_away": "H",
                                "score": "1-0",
                            },
                            {
                                "date": "September 16, 2023",
                                "home_team": "SS Lazio",
                                "away_team": "Juventus FC",
                                "stage": "Giornata 4",
                                "match_url": "/matches/lazio-juventus",
                                "full_url": "https://footballia.eu/matches/lazio-juventus",
                                "has_video": True,
                                "home_away": "A",
                                "score": "0-1",
                            },
                        ],
                    },
                    {
                        "name": "Europa League",
                        "matches": [
                            {
                                "date": "February 22, 2024",
                                "home_team": "Juventus FC",
                                "away_team": "Freiburg",
                                "stage": "Round of 32",
                                "match_url": "/matches/juventus-freiburg",
                                "full_url": "https://footballia.eu/matches/juventus-freiburg",
                                "has_video": True,
                                "home_away": "H",
                                "score": "1-0",
                            },
                        ],
                    },
                ],
            },
        ],
        "scrape_success": True,
    }


class TestParseMatchRow:
    """Tests for _parse_match_row() team name extraction."""

    def test_parse_vs_dot_separator(self, navigator):
        """Link text with 'vs.' should correctly split into two teams."""
        result = navigator._parse_match_row(
            "Juventus FC vs. FC Nantes", "February 16, 2023 ...", "/matches/juventus-fc-nantes"
        )
        assert result is not None
        assert result["home_team"] == "Juventus FC"
        assert result["away_team"] == "FC Nantes"

    def test_parse_vs_no_dot_separator(self, navigator):
        """Link text with 'vs' (no dot) should split correctly."""
        result = navigator._parse_match_row(
            "AS Roma vs Juventus FC", "March 05, 2023 ...", "/matches/roma-juventus"
        )
        assert result is not None
        assert result["home_team"] == "AS Roma"
        assert result["away_team"] == "Juventus FC"

    def test_parse_dash_separator(self, navigator):
        """Link text with ' - ' should split correctly."""
        result = navigator._parse_match_row(
            "Juventus FC - PSV Eindhoven", "...", "/matches/juventus-psv"
        )
        assert result is not None
        assert result["home_team"] == "Juventus FC"
        assert result["away_team"] == "PSV Eindhoven"

    def test_parse_single_team_no_separator(self, navigator):
        """Link text with no separator should set home_team only."""
        result = navigator._parse_match_row(
            "Juventus FC", "...", "/matches/juventus"
        )
        assert result is not None
        assert result["home_team"] == "Juventus FC"
        assert result["away_team"] == ""

    def test_parse_extracts_date(self, navigator):
        """Should extract date from row text."""
        result = navigator._parse_match_row(
            "Team A vs. Team B",
            "September 17, 2024 Team A vs. Team B Europa League",
            "/matches/team-a-team-b"
        )
        assert result is not None
        assert result["date"] == "September 17, 2024"

    def test_parse_invalid_href_returns_none(self, navigator):
        """Invalid href should return None."""
        result = navigator._parse_match_row("Some text", "", "/teams/juventus")
        assert result is None

    def test_parse_no_trailing_vs(self, navigator):
        """Team names should never contain trailing 'vs' or 'vs.'."""
        result = navigator._parse_match_row(
            "FC Nantes vs. Juventus FC", "...", "/matches/nantes-juventus"
        )
        assert result is not None
        assert not result["home_team"].endswith("vs")
        assert not result["home_team"].endswith("vs.")
        assert not result["away_team"].endswith("vs")
        assert not result["away_team"].endswith("vs.")


class TestNavigatorFilterTeamMatches:

    def test_filter_by_season(self, navigator, sample_team_data):
        """Filtering by season should return only matches from that season."""
        results = navigator.filter_team_matches(sample_team_data, season="2024-2025")
        assert len(results) == 4  # 2 Serie A + 1 CL + 1 Coppa Italia
        for m in results:
            assert m["season"] == "2024-2025"

    def test_filter_by_competition(self, navigator, sample_team_data):
        """Filtering by competition should return only matches from that competition."""
        results = navigator.filter_team_matches(sample_team_data, competition="Serie A")
        assert len(results) == 4  # 2 from 2024-2025 + 2 from 2023-2024
        for m in results:
            assert m["competition"] == "Serie A"

    def test_filter_by_season_and_competition(self, navigator, sample_team_data):
        """Combining season + competition should narrow results."""
        results = navigator.filter_team_matches(
            sample_team_data, season="2024-2025", competition="Serie A"
        )
        assert len(results) == 2
        for m in results:
            assert m["season"] == "2024-2025"
            assert m["competition"] == "Serie A"

    def test_filter_champions_league_only(self, navigator, sample_team_data):
        """Filtering Champions League should return exactly 1 match."""
        results = navigator.filter_team_matches(
            sample_team_data, competition="Champions League"
        )
        assert len(results) == 1
        assert results[0]["away_team"] == "PSV Eindhoven"

    def test_filter_no_results(self, navigator, sample_team_data):
        """Filtering for nonexistent competition should return empty."""
        results = navigator.filter_team_matches(
            sample_team_data, competition="Premier League"
        )
        assert len(results) == 0

    def test_no_filter_returns_all(self, navigator, sample_team_data):
        """No filters should return all matches."""
        results = navigator.filter_team_matches(sample_team_data)
        assert len(results) == 7

    def test_filter_by_home_away(self, navigator, sample_team_data):
        """Filtering by home/away should work."""
        results = navigator.filter_team_matches(sample_team_data, home_away="A")
        assert len(results) == 1
        assert results[0]["away_team"] == "Juventus FC"

    def test_filter_results_have_season_and_competition(self, navigator, sample_team_data):
        """Filtered results should have season and competition fields set."""
        results = navigator.filter_team_matches(sample_team_data, season="2023-2024")
        assert len(results) == 3
        for m in results:
            assert "season" in m
            assert "competition" in m
            assert m["season"] == "2023-2024"

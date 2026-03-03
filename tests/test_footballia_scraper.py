"""Tests for the Footballia page scraper module."""
import json


class TestFootballiaScraper:
    """
    These tests validate the data parsing logic of the scraper.
    They do NOT require a running browser or Footballia connection.
    They test the helper methods that parse scraped DOM data.
    """

    def test_resolve_goal_teams_basic(self):
        from backend.footballia_scraper import FootballiaScraper
        scraper = FootballiaScraper()

        match_data = {
            "home_team": "Juventus FC",
            "away_team": "Celtic FC",
            "home_lineup": [
                {"number": 10, "name": "Platini"},
                {"number": 9, "name": "Rossi"},
            ],
            "away_lineup": [
                {"number": 7, "name": "Burns"},
            ],
            "goals": [
                {"minute": 23, "scorer": "Platini", "team": "unknown"},
                {"minute": 67, "scorer": "Burns", "team": "unknown"},
            ],
        }

        resolved = scraper.resolve_goal_teams(match_data)

        assert resolved[0]["team"] == "home"
        assert resolved[0]["scorer"] == "Platini"
        assert resolved[1]["team"] == "away"
        assert resolved[1]["scorer"] == "Burns"

    def test_resolve_goal_teams_no_match(self):
        from backend.footballia_scraper import FootballiaScraper
        scraper = FootballiaScraper()

        match_data = {
            "home_team": "Team A",
            "away_team": "Team B",
            "home_lineup": [{"name": "Player1"}],
            "away_lineup": [{"name": "Player2"}],
            "goals": [{"minute": 10, "scorer": "UnknownPlayer", "team": "unknown"}],
        }

        resolved = scraper.resolve_goal_teams(match_data)
        # Unknown scorer stays as "unknown"
        assert resolved[0]["team"] == "unknown"

    def test_resolve_goal_teams_empty_goals(self):
        from backend.footballia_scraper import FootballiaScraper
        scraper = FootballiaScraper()

        match_data = {
            "home_lineup": [],
            "away_lineup": [],
            "goals": [],
        }
        resolved = scraper.resolve_goal_teams(match_data)
        assert resolved == []

    def test_parse_match_row(self):
        """Test the navigator's match row parser."""
        from backend.footballia_navigator import FootballiaNavigator
        nav = FootballiaNavigator()

        result = nav._parse_match_row(
            link_text="Juventus FC - Celtic FC",
            row_text="September 30, 1981  R32 2nd leg  Juventus FC - Celtic FC",
            href="/matches/juventus-fc-celtic-fc",
        )

        assert result is not None
        assert result["home_team"] == "Juventus FC"
        assert result["away_team"] == "Celtic FC"
        assert "1981" in result["date"]
        assert result["stage"] == "R32 2nd leg"

    def test_parse_match_row_vs_format(self):
        from backend.footballia_navigator import FootballiaNavigator
        nav = FootballiaNavigator()

        result = nav._parse_match_row(
            link_text="Real Madrid vs Barcelona",
            row_text="Real Madrid vs Barcelona",
            href="/matches/real-madrid-barcelona",
        )

        assert result["home_team"] == "Real Madrid"
        assert result["away_team"] == "Barcelona"

    def test_filter_matches(self):
        from backend.footballia_navigator import FootballiaNavigator
        nav = FootballiaNavigator()

        data = {
            "clubs": [
                {
                    "name": "Juventus FC",
                    "role": "coach",
                    "seasons": [
                        {
                            "season": "1981-1982",
                            "competition": "European Cup",
                            "matches": [
                                {"home_team": "Juventus", "away_team": "Celtic", "date": "1981"},
                                {"home_team": "Juventus", "away_team": "Anderlecht", "date": "1982"},
                            ],
                        },
                        {
                            "season": "1981-1982",
                            "competition": "Serie A",
                            "matches": [
                                {"home_team": "Juventus", "away_team": "Napoli", "date": "1981"},
                            ],
                        },
                    ],
                },
                {
                    "name": "Inter",
                    "role": "coach",
                    "seasons": [
                        {
                            "season": "1979-1980",
                            "competition": "Serie A",
                            "matches": [
                                {"home_team": "Inter", "away_team": "Milan", "date": "1979"},
                            ],
                        },
                    ],
                },
            ],
        }

        # Filter by club
        juve = nav.filter_matches(data, club="Juventus")
        assert len(juve) == 3

        # Filter by season
        season_81 = nav.filter_matches(data, season="1981-1982")
        assert len(season_81) == 3

        # Filter by competition
        ec = nav.filter_matches(data, competition="European Cup")
        assert len(ec) == 2

        # Combined filters
        juve_ec = nav.filter_matches(data, club="Juventus", competition="European Cup")
        assert len(juve_ec) == 2

        # No matches
        empty = nav.filter_matches(data, club="Bayern")
        assert len(empty) == 0

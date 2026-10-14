"""
Tests for retrieval - the RAG part.
"""

import pytest

from rag.retriever import retriever, tokenize


class TestTokenizer:
    def test_it_lowercases_and_splits(self):
        # "from" is a stopword, so it should not survive
        assert tokenize("Sunset VIEW from Nahargarh!") == ["sunset", "view", "nahargarh"]

    def test_it_drops_stopwords(self):
        assert "the" not in tokenize("the fort and the lake")

    def test_it_drops_very_short_words(self):
        assert tokenize("go up to it") == []


class TestRetriever:
    def test_the_corpus_loaded(self):
        assert len(retriever.documents) > 20

    def test_every_document_has_the_fields_we_need(self):
        for doc in retriever.documents:
            assert doc.get("city")
            assert doc.get("title")
            assert doc.get("text")

    def test_it_finds_the_right_city(self):
        results = retriever.search("beaches and relaxing", city="goa", top_k=3)
        assert results
        assert results[0]["city"] == "goa"

    def test_the_city_bonus_actually_works(self):
        """A budget query for Manali should surface Manali tips, not Goa ones."""
        results = retriever.search("budget stay", city="manali", top_k=3)
        assert any(r["city"] == "manali" for r in results)

    def test_results_are_sorted_by_score(self):
        results = retriever.search("fort heritage sunset", city="jaipur", top_k=5)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_nonsense_returns_little_or_nothing(self):
        results = retriever.search("qqqqq zzzzz xxxxx", city="", top_k=5)
        assert len(results) <= 2

    def test_an_empty_query_does_not_crash(self):
        assert retriever.search("", city="", top_k=3) == []

    def test_prompt_context_is_readable(self):
        results = retriever.search("food", city="jaipur", top_k=2)
        text = retriever.as_prompt_context(results)
        assert "Local tips" in text or "No local tips" in text


# ---------------------------------------------------------------------------
# Place quality filtering
#
# These tests exist because a real Dehradun itinerary came back containing
# "2008 Dehradun Municipal Corporation election" as a 90-minute stop, four
# private schools, and a village 200km away. Every case below is taken from
# that actual bad output.
# ---------------------------------------------------------------------------

from tools.places import _kind_from_title, _looks_worth_visiting


class TestPlaceFiltering:
    def test_administrative_pages_are_dropped(self):
        for title in [
            "2008 Dehradun Municipal Corporation election",
            "Battle of Nalapani",
            "National Hydrographic Office",
            "Lok Bhavan, Dehradun",
            "List of forts in Rajasthan",
        ]:
            assert _looks_worth_visiting(title) == 0, f"{title} should be dropped"

    def test_schools_are_dropped(self):
        for title in ["The Doon School", "Welham Girls' School",
                      "St Joseph's Academy, Dehradun", "The Asian School"]:
            assert _looks_worth_visiting(title) == 0, f"{title} should be dropped"

    def test_whole_states_are_dropped(self):
        for title in ["Uttarakhand", "Rajasthan", "India"]:
            assert _looks_worth_visiting(title) == 0

    def test_real_attractions_are_kept(self):
        for title in ["Tapkeshwar Temple", "Hawa Mahal", "Nahargarh Fort",
                      "Robber's Cave, India", "Malsi Deer Park",
                      "Uttara Museum of Contemporary Art"]:
            assert _looks_worth_visiting(title) == 2, f"{title} should be kept"

    def test_known_landmarks_beat_the_deny_list(self):
        """Forest Research Institute contains 'research' AND 'institute'."""
        assert _looks_worth_visiting("Forest Research Institute") == 2
        assert _looks_worth_visiting("Rashtrapati Niketan") == 2

    def test_unknown_titles_are_uncertain_not_rejected(self):
        assert _looks_worth_visiting("Sahastradhara") == 1
        assert _looks_worth_visiting("Clement Town") == 1

    # -----------------------------------------------------------------
    # Description-aware scoring: the title alone can be genuinely
    # ambiguous (a person's name, a generic local name). Wikipedia's own
    # one-line description is a much stronger signal than guessing from
    # the title, and used to be ignored entirely.
    # -----------------------------------------------------------------
    def test_description_promotes_an_ambiguous_title(self):
        """Title alone gives no keyword hit and would only score 1
        (needs a lucky thumbnail); the description makes it a clear 2."""
        assert _looks_worth_visiting("Chausath Yogini", description="") == 1
        assert _looks_worth_visiting(
            "Chausath Yogini", description="Hindu temple in Madhya Pradesh, India"
        ) == 2

    def test_description_drops_an_ambiguous_title(self):
        """A locality name with no keyword in the title used to slip
        through as 'uncertain' the moment it had any thumbnail at all."""
        assert _looks_worth_visiting("Rajendra Nagar", description="") == 1
        assert _looks_worth_visiting(
            "Rajendra Nagar",
            description="ward in Dehradun Municipal Corporation, Uttarakhand",
        ) == 0

    def test_known_landmark_still_beats_a_generic_description(self):
        assert _looks_worth_visiting(
            "Forest Research Institute", description="research institute in Dehradun"
        ) == 2

    def test_kind_is_guessed_for_the_day_planner(self):
        assert _kind_from_title("Tapkeshwar Temple") == "place_of_worship"
        assert _kind_from_title("Nahargarh Fort") == "fort"
        assert _kind_from_title("Albert Hall Museum") == "museum"
        assert _kind_from_title("Malsi Deer Park") == "park"
        assert _kind_from_title("Nahargarh Viewpoint") == "viewpoint"


class TestWikipediaNearbyUsesDescription:
    """Integration check that wikipedia_nearby actually asks for and uses
    the description field, not just that the scoring function can."""

    @pytest.mark.asyncio
    async def test_description_field_is_requested_and_applied(self, monkeypatch):
        from tools import places

        fake_response = {
            "query": {
                "pages": {
                    "1": {
                        "title": "Rajendra Nagar",
                        "description": "ward in Dehradun Municipal Corporation",
                        "thumbnail": {"source": "https://example.com/x.jpg"},
                        "coordinates": [{"lat": 30.32, "lon": 78.03}],
                    },
                    "2": {
                        "title": "Chausath Yogini",
                        "description": "Hindu temple in Madhya Pradesh, India",
                        "coordinates": [{"lat": 23.15, "lon": 79.93}],
                        # no thumbnail - description alone should still keep it
                    },
                }
            }
        }

        async def fake_get_json(url, params=None, label="", ttl=None, **kw):
            assert "description" in params["prop"]
            return fake_response
        monkeypatch.setattr(places.webclient, "get_json", fake_get_json)

        result = await places.wikipedia_nearby(30.32, 78.03)
        names = [p["name"] for p in result]

        assert "Rajendra Nagar" not in names  # dropped: description says it's an admin ward
        assert "Chausath Yogini" in names      # kept: description says it's a temple


class TestFareConversion:
    def test_duffel_gbp_becomes_sensible_rupees(self):
        """A GBP 168 fare was being shown as Rs 168, which is nonsense."""
        from tools.transport import TO_INR
        assert int(168 * TO_INR["GBP"]) > 15000

    def test_iso_durations_are_made_readable(self):
        from tools.transport import _readable_duration
        assert _readable_duration("PT2H15M") == "2h 15m"
        assert _readable_duration("PT45M") == "0h 45m"

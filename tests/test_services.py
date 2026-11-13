from ci_assistant.models import Document, SourceRef, Verdict
from ci_assistant.services.extraction_service import RegexAttributeExtractor


def test_unit_normalisation_on_load(container):
    z = container.repository.get("COMP-DEMO-Z")
    assert abs(z.get("max_flow").value - 98.4) < 0.5  # 26 gpm -> l/min
    assert abs(z.get("max_pressure").value - 248.2) < 0.5  # 3600 psi -> bar
    assert z.get("max_flow").unit == "l/min"


def test_matcher_ranks_closest_first(container):
    repo = container.repository
    a = container.matcher.rank(repo.get("DANFOSS-DEMO-A"), repo.list_competitors())
    b = container.matcher.rank(repo.get("DANFOSS-DEMO-B"), repo.list_competitors())
    assert a[0].product.id == "COMP-DEMO-X"
    assert b[0].product.id == "COMP-DEMO-Y"


def test_comparison_flags_missing_and_keeps_sources(container):
    repo = container.repository
    result = container.engine.compare(repo.get("DANFOSS-DEMO-A"), repo.get("COMP-DEMO-Z"))
    by_key = {r.key: r for r in result.rows}
    assert by_key["displacement"].verdict is Verdict.MISSING
    assert by_key["max_flow"].verdict is Verdict.MATCH
    assert by_key["max_flow"].left_source is not None
    assert any("Displacement" in u for u in result.uncertainties)


def test_regex_extractor_returns_sourced_values():
    doc = Document("d", "Max flow: 26 gpm\nMax pressure: 3600 psi", SourceRef(url="file://d"))
    attrs = RegexAttributeExtractor().extract(doc)
    assert abs(attrs["max_flow"].value - 98.4) < 0.5
    assert attrs["max_flow"].source.url == "file://d"

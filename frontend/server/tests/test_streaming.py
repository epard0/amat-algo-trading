from server.app import redact
from server.streaming import OutputLines


def test_secret_and_utf8_can_cross_pipe_chunks(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "sensitive-broker-value")
    lines = OutputLines(redact)
    assert lines.feed(b"broker=sensitive-") == ""
    output = lines.feed(b"broker-value\nprice \xe2")
    assert "sensitive" not in output and "[redacted]" in output
    assert lines.feed(b"\x82\xac\n") == "price €\n"
    assert lines.feed(b"last line") == ""
    assert lines.feed(b"", final=True) == "last line"


def test_unbounded_line_is_omitted_without_leaking_suffix():
    lines = OutputLines(redact, limit=8)
    assert "omitted" in lines.feed(b"verylongsecret")
    assert lines.feed(b"continuation") == ""
    assert lines.feed(b"\nnormal\n") == "normal\n"

"""Episodes from JSON Lines."""

import pytest
from conftest import DATA

from schemacast.episode import Episode, EpisodeFileError, read_episodes


def test_reads_episodes_and_skips_blank_lines():
    episodes = list(read_episodes(DATA / "episodes.jsonl"))
    assert [e.source_id for e in episodes] == ["msg-1", "msg-2"]
    assert episodes[0].reference_time is not None
    assert episodes[0].metadata == {"channel": "line-2"}
    assert episodes[1].context[0].source_id == "msg-1"


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("{not json", "not JSON"),
        ('{"source_id": "x"}', "text"),
        ('{"source_id": "x", "text": "t", "extra": 1}', "extra"),
    ],
)
def test_bad_lines_name_their_line_number(tmp_path, line, reason):
    path = tmp_path / "episodes.jsonl"
    path.write_text('{"source_id": "ok", "text": "fine"}\n' + line + "\n")
    with pytest.raises(EpisodeFileError, match=reason) as info:
        list(read_episodes(path))
    assert info.value.line == 2


def test_text_and_source_id_are_required():
    with pytest.raises(ValueError):
        Episode(text="", source_id="x")
    with pytest.raises(ValueError):
        Episode(text="t", source_id="")

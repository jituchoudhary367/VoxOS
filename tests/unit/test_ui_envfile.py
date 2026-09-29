"""The Settings page edits .env in place: comments, order and unknown keys survive."""

from __future__ import annotations

from pathlib import Path

from voxos.ui.envfile import EnvFile, mask

SAMPLE = """\
# LLM
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_first
GROQ_API_KEYS=gsk_second, gsk_third
GROQ_API_KEY_2=gsk_numbered
# GROQ_API_KEY=commented_out

# Voice
DEEPGRAM_API_KEY="dg 123"  # quoted
VOICE_HOTKEY=ctrl+space  # inline comment
"""


def _env(tmp_path: Path, text: str = SAMPLE) -> EnvFile:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return EnvFile.load(path)


def test_reads_values_ignoring_comments_and_quotes(tmp_path: Path) -> None:
    values = _env(tmp_path).values()
    assert values["GROQ_API_KEY"] == "gsk_first"
    assert values["DEEPGRAM_API_KEY"] == "dg 123"
    assert values["VOICE_HOTKEY"] == "ctrl+space"


def test_groq_keys_are_collected_in_order_without_duplicates(tmp_path: Path) -> None:
    assert _env(tmp_path).groq_keys() == ["gsk_first", "gsk_second", "gsk_third", "gsk_numbered"]


def test_set_rewrites_the_line_in_place_and_keeps_comments(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env.set("VOICE_HOTKEY", "alt+v")
    env.set("NEW_KEY", "value")
    env.save()
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "# LLM"
    assert "# GROQ_API_KEY=commented_out" in lines
    assert lines[lines.index("# Voice") + 2] == "VOICE_HOTKEY=alt+v"
    assert lines[-1] == "NEW_KEY=value"
    assert EnvFile.load(tmp_path / ".env").get("VOICE_HOTKEY") == "alt+v"


def test_values_with_spaces_or_quotes_round_trip(tmp_path: Path) -> None:
    env = _env(tmp_path, "")
    env.set("A", 'say "hi" # not a comment')
    env.save()
    assert EnvFile.load(tmp_path / ".env").get("A") == 'say "hi" # not a comment'


def test_set_groq_keys_consolidates_and_drops_numbered_copies(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env.set_groq_keys(["gsk_second", "gsk_new", "gsk_second", ""])
    env.save()
    reloaded = EnvFile.load(tmp_path / ".env")
    assert reloaded.groq_keys() == ["gsk_second", "gsk_new"]
    assert "GROQ_API_KEY_2" not in reloaded.values()
    # The commented-out line is left alone.
    assert "# GROQ_API_KEY=commented_out" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_removing_every_groq_key_leaves_them_empty(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env.set_groq_keys([])
    assert env.groq_keys() == []


def test_missing_file_is_created_on_save(tmp_path: Path) -> None:
    env = EnvFile.load(tmp_path / "nested" / ".env")
    assert env.values() == {}
    env.set("X", "1")
    env.save()
    assert (tmp_path / "nested" / ".env").read_text(encoding="utf-8") == "X=1\n"


def test_mask_never_reveals_a_usable_key() -> None:
    assert mask("gsk_abcdefghijklmnop1234") == "gsk_…1234"
    assert mask("short") == "•••••"
    assert mask("") == ""

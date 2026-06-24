import pytest
import yaml

from es import vault_client


def test_sanitize_strips_illegal_chars():
    assert vault_client._sanitize_title('a/b:c*?"<>|d') == "abcd"


def test_sanitize_keeps_spaces_and_unicode():
    assert vault_client._sanitize_title("Update on EverStone 🛒") == "Update on EverStone 🛒"


def test_unique_filename_no_collision(tmp_path):
    assert vault_client._unique_filename(tmp_path, "Note") == "Note.md"


def test_unique_filename_collision_suffixes(tmp_path):
    (tmp_path / "Note.md").write_text("x")
    assert vault_client._unique_filename(tmp_path, "Note") == "Note 2.md"
    (tmp_path / "Note 2.md").write_text("x")
    assert vault_client._unique_filename(tmp_path, "Note") == "Note 3.md"


def test_normalize_topic_wraps_bare_name():
    assert vault_client._normalize_topic("EverStone") == "[[EverStone]]"


def test_normalize_topic_keeps_existing_wikilink():
    assert vault_client._normalize_topic("[[EverStone]]") == "[[EverStone]]"


def test_normalize_topic_rejects_empty():
    with pytest.raises(vault_client.InvalidTopic):
        vault_client._normalize_topic("   ")


def test_render_frontmatter_quotes_topic_links():
    fm = vault_client._render_frontmatter(
        created="2026-06-21T14:32", author="everstone",
        tags=["everstone"], topics=["EverStone", "[[Allison]]"], meta={"mood": "up"})
    assert "author: everstone" in fm
    assert "mood: up" in fm
    loaded = yaml.safe_load(fm.strip().strip("-"))
    assert loaded["topics"] == ["[[EverStone]]", "[[Allison]]"]
    assert loaded["tags"] == ["everstone"]


def test_render_frontmatter_omits_empty_topics():
    fm = vault_client._render_frontmatter(
        created="2026-06-21T14:32", author="everstone", tags=[], topics=[], meta={})
    assert "topics:" not in fm
    assert "tags:" not in fm


@pytest.fixture
def vc(tmp_path):
    return vault_client.VaultClient(tmp_path, "MyVault")


def test_write_journal_creates_dated_file(vc, tmp_path, monkeypatch):
    monkeypatch.setattr(vault_client, "_today", lambda: "2026-06-21")
    monkeypatch.setattr(vault_client, "_now_iso", lambda: "2026-06-21T14:32")
    out = vc.write_journal("Update on EverStone notes model", "the body",
                           tags=["everstone"], topics=["EverStone"], meta=None)
    p = tmp_path / "journal" / "2026-06-21" / "Update on EverStone notes model.md"
    assert p.exists()
    text = p.read_text()
    assert "the body" in text
    assert "topics:" in text and "[[EverStone]]" in text
    assert "author: everstone" in text
    assert out["path"].endswith("Update on EverStone notes model.md")
    assert out["obsidian_deeplink"].startswith("obsidian://open?vault=MyVault")


def test_write_journal_collision_suffix(vc, tmp_path, monkeypatch):
    monkeypatch.setattr(vault_client, "_today", lambda: "2026-06-21")
    monkeypatch.setattr(vault_client, "_now_iso", lambda: "2026-06-21T14:32")
    vc.write_journal("Same title", "a", tags=None, topics=None, meta=None)
    out2 = vc.write_journal("Same title", "b", tags=None, topics=None, meta=None)
    assert out2["path"].endswith("Same title 2.md")


def test_write_topic_creates_empty(vc, tmp_path):
    out = vc.write_topic("Home network")
    p = tmp_path / "topics" / "Home network.md"
    assert p.exists() and out["created"] is True


def test_write_topic_sets_body(vc, tmp_path):
    vc.write_topic("Home network")
    out = vc.write_topic("Home network", body="Router in the closet.")
    assert (tmp_path / "topics" / "Home network.md").read_text().strip() == "Router in the closet."
    assert out["created"] is False


def test_write_topic_appends_update(vc, tmp_path, monkeypatch):
    monkeypatch.setattr(vault_client, "_today", lambda: "2026-06-21")
    vc.write_topic("Home network", body="State.")
    vc.write_topic("Home network", update="Swapped the router.")
    text = (tmp_path / "topics" / "Home network.md").read_text()
    assert "## Updates" in text
    assert "- 2026-06-21: Swapped the router." in text
    assert "State." in text


def test_list_topics_lists_names(vc):
    vc.write_topic("EverStone"); vc.write_topic("Home network"); vc.write_topic("Allison")
    assert sorted(vc.list_topics()) == ["Allison", "EverStone", "Home network"]


def test_list_topics_empty_when_no_folder(vc):
    assert vc.list_topics() == []


def test_list_topics_like_fuzzy(vc):
    vc.write_topic("Home network"); vc.write_topic("EverStone")
    assert vc.list_topics(like="home") == ["Home network"]
    assert vc.list_topics(like="netwrk") == ["Home network"]


def test_read_note_by_relpath(vc, tmp_path, monkeypatch):
    monkeypatch.setattr(vault_client, "_today", lambda: "2026-06-21")
    monkeypatch.setattr(vault_client, "_now_iso", lambda: "2026-06-21T14:32")
    out = vc.write_journal("A note", "hello body", tags=["x"], topics=["EverStone"], meta=None)
    got = vc.read_note(out["path"])
    assert got["body"].strip() == "hello body"
    assert got["frontmatter"]["author"] == "everstone"
    assert got["frontmatter"]["topics"] == ["[[EverStone]]"]


def test_read_note_by_topic_name(vc):
    vc.write_topic("Home network", body="Router state.")
    got = vc.read_note("Home network")
    assert got["body"].strip() == "Router state."


def test_read_note_missing_topic_raises(vc):
    with pytest.raises(vault_client.NoteNotFound):
        vc.read_note("Nonexistent")


def _seed_two_days(vc, monkeypatch):
    monkeypatch.setattr(vault_client, "_now_iso", lambda: "2026-06-20T09:00")
    monkeypatch.setattr(vault_client, "_today", lambda: "2026-06-20")
    vc.write_journal("Day20 entry", "b", tags=None, topics=["EverStone"], meta=None)
    monkeypatch.setattr(vault_client, "_now_iso", lambda: "2026-06-21T09:00")
    monkeypatch.setattr(vault_client, "_today", lambda: "2026-06-21")
    vc.write_journal("Day21 entry", "b", tags=None, topics=None, meta=None)


def test_list_journal_all(vc, monkeypatch):
    _seed_two_days(vc, monkeypatch)
    assert {e["title"] for e in vc.list_journal()} == {"Day20 entry", "Day21 entry"}


def test_list_journal_by_day(vc, monkeypatch):
    _seed_two_days(vc, monkeypatch)
    assert [e["title"] for e in vc.list_journal(day="2026-06-20")] == ["Day20 entry"]


def test_list_journal_by_topic(vc, monkeypatch):
    _seed_two_days(vc, monkeypatch)
    assert [e["title"] for e in vc.list_journal(topic="EverStone")] == ["Day20 entry"]


def test_list_journal_since(vc, monkeypatch):
    _seed_two_days(vc, monkeypatch)
    assert [e["title"] for e in vc.list_journal(since="2026-06-21")] == ["Day21 entry"]

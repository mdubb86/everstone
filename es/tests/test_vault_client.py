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
    p = tmp_path / "Journal" / "2026-06-21" / "Update on EverStone notes model.md"
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
    p = tmp_path / "Topics" / "Home network.md"
    assert p.exists() and out["created"] is True


def test_write_topic_sets_body(vc, tmp_path):
    vc.write_topic("Home network")
    out = vc.write_topic("Home network", body="Router in the closet.")
    assert (tmp_path / "Topics" / "Home network.md").read_text().strip() == "Router in the closet."
    assert out["created"] is False


def test_write_topic_appends_update(vc, tmp_path, monkeypatch):
    monkeypatch.setattr(vault_client, "_today", lambda: "2026-06-21")
    vc.write_topic("Home network", body="State.")
    vc.write_topic("Home network", update="Swapped the router.")
    text = (tmp_path / "Topics" / "Home network.md").read_text()
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


def test_journal_folder_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setattr(vault_client, "_today", lambda: "2026-07-04")
    monkeypatch.setattr(vault_client, "_now_iso", lambda: "2026-07-04T09:00")
    vc = vault_client.VaultClient(tmp_path, "V", journal_folder="Diary")
    vc.write_journal("Note", "b", tags=None, topics=None, meta=None)
    assert (tmp_path / "Diary" / "2026-07-04" / "Note.md").exists()


def test_write_topic_defaults_to_first_category(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics", "People"))
    vc.write_topic("Kitchen fridge", body="state")
    assert (tmp_path / "Topics" / "Kitchen fridge.md").exists()


def test_write_topic_files_under_named_category(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics", "People"))
    vc.write_topic("Allison", body="s", category="People")
    assert (tmp_path / "People" / "Allison.md").exists()


def test_write_topic_rejects_offlist_category(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics",))
    with pytest.raises(vault_client.InvalidCategory):
        vc.write_topic("X", body="s", category="Projects")


def test_write_topic_existing_updates_in_place_ignoring_category(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics", "People"))
    vc.write_topic("Allison", body="s", category="People")
    vc.write_topic("Allison", update="note")  # no category → must find existing in People
    assert "note" in (tmp_path / "People" / "Allison.md").read_text()
    assert not (tmp_path / "Topics" / "Allison.md").exists()


def test_list_topics_scans_all_categories(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics", "People"))
    vc.write_topic("Fridge"); vc.write_topic("Allison", category="People")
    assert sorted(vc.list_topics()) == ["Allison", "Fridge"]


def _make_folder_note(base, name, body="x"):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(body)
    return d / f"{name}.md"


def test_resolve_finds_folder_note_topic(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics",))
    _make_folder_note(tmp_path / "Topics", "Kitchen fridge", "body")
    got = vc.read_note("Kitchen fridge")
    assert got["body"].strip() == "body"


def test_resolve_stale_flat_relpath_falls_back_to_folder(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics",))
    _make_folder_note(tmp_path / "Topics", "Fridge", "b")
    got = vc.read_note("Topics/Fridge.md")   # the pre-promotion handle
    assert got["body"].strip() == "b"


def test_list_topics_includes_folder_notes(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics",))
    vc.write_topic("Flat")
    _make_folder_note(tmp_path / "Topics", "Foldered")
    assert sorted(vc.list_topics()) == ["Flat", "Foldered"]


def test_list_journal_includes_folder_notes(tmp_path, monkeypatch):
    vc = vault_client.VaultClient(tmp_path, "V")
    monkeypatch.setattr(vault_client, "_today", lambda: "2026-07-04")
    monkeypatch.setattr(vault_client, "_now_iso", lambda: "2026-07-04T09:00")
    vc.write_journal("Flat entry", "b", tags=None, topics=None, meta=None)
    _make_folder_note(tmp_path / "Journal" / "2026-07-04", "Foldered entry", "---\n---\nb")
    assert {e["title"] for e in vc.list_journal()} == {"Flat entry", "Foldered entry"}


def test_attach_promotes_flat_topic_and_copies(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics",))
    vc.write_topic("Kitchen fridge", body="state")
    src = tmp_path / "src.pdf"; src.write_bytes(b"%PDF-1.4")
    out = vc.attach("Kitchen fridge", str(src))
    assert (tmp_path / "Topics" / "Kitchen fridge" / "Kitchen fridge.md").exists()
    assert not (tmp_path / "Topics" / "Kitchen fridge.md").exists()
    assert (tmp_path / "Topics" / "Kitchen fridge" / "src.pdf").exists()
    assert out["ref"] == "![[src.pdf]]"
    assert src.exists()  # original left in place (copied, not moved)


def test_attach_second_file_no_double_promote(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics",))
    vc.write_topic("Fridge", body="s")
    s1 = tmp_path / "a.png"; s1.write_bytes(b"1")
    s2 = tmp_path / "b.png"; s2.write_bytes(b"2")
    vc.attach("Fridge", str(s1))
    vc.attach("Fridge", str(s2))
    folder = tmp_path / "Topics" / "Fridge"
    assert (folder / "a.png").exists() and (folder / "b.png").exists()
    assert (folder / "Fridge.md").exists()


def test_attach_collision_suffixes(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics",))
    vc.write_topic("Fridge", body="s")
    for _ in range(2):
        s = tmp_path / "dup.png"; s.write_bytes(b"x")
        vc.attach("Fridge", str(s))
    folder = tmp_path / "Topics" / "Fridge"
    assert (folder / "dup.png").exists() and (folder / "dup 2.png").exists()


def test_attach_promotes_journal_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(vault_client, "_today", lambda: "2026-07-04")
    monkeypatch.setattr(vault_client, "_now_iso", lambda: "2026-07-04T09:00")
    vc = vault_client.VaultClient(tmp_path, "V")
    out = vc.write_journal("Router swap", "b", tags=None, topics=None, meta=None)
    s = tmp_path / "photo.jpg"; s.write_bytes(b"x")
    vc.attach(out["path"], str(s))
    day = tmp_path / "Journal" / "2026-07-04"
    assert (day / "Router swap" / "Router swap.md").exists()
    assert (day / "Router swap" / "photo.jpg").exists()


def test_attach_missing_source_raises(tmp_path):
    vc = vault_client.VaultClient(tmp_path, "V", categories=("Topics",))
    vc.write_topic("Fridge", body="s")
    with pytest.raises(vault_client.AttachmentSourceNotFound):
        vc.attach("Fridge", str(tmp_path / "nope.pdf"))

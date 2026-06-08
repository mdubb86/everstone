from es.deeplink import build_deeplink

def test_simple():
    assert build_deeplink("everstone", "Inbox.md") == "obsidian://open?vault=everstone&file=Inbox.md"

def test_encoded():
    assert build_deeplink("My Vault", "Projects/Q4 Report.md") == \
        "obsidian://open?vault=My%20Vault&file=Projects%2FQ4%20Report.md"

def test_strip_leading_slash():
    assert build_deeplink("v", "/a.md") == "obsidian://open?vault=v&file=a.md"

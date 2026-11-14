import json, os, secrets, sqlite3, time

_ACCESS_TTL = 3600


class OAuthStore:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
          CREATE TABLE IF NOT EXISTS clients(client_id TEXT PRIMARY KEY, redirect_uris TEXT);
          CREATE TABLE IF NOT EXISTS codes(code TEXT PRIMARY KEY, client_id TEXT,
              redirect_uri TEXT, code_challenge TEXT, scope TEXT);
          CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY, kind TEXT,
              client_id TEXT, scope TEXT, access_expires_at REAL);
        """)

    def register_client(self, redirect_uris):
        cid = "es-" + secrets.token_urlsafe(16)
        self._db.execute("INSERT INTO clients VALUES(?,?)", (cid, json.dumps(redirect_uris)))
        return cid

    def get_client(self, client_id):
        row = self._db.execute("SELECT client_id, redirect_uris FROM clients WHERE client_id=?",
                               (client_id,)).fetchone()
        return {"client_id": row[0], "redirect_uris": json.loads(row[1])} if row else None

    def save_code(self, code, client_id, redirect_uri, code_challenge, scope):
        self._db.execute("INSERT INTO codes VALUES(?,?,?,?,?)",
                         (code, client_id, redirect_uri, code_challenge, scope))

    def take_code(self, code):
        row = self._db.execute(
            "SELECT client_id, redirect_uri, code_challenge, scope FROM codes WHERE code=?",
            (code,)).fetchone()
        if not row:
            return None
        self._db.execute("DELETE FROM codes WHERE code=?", (code,))
        return {"client_id": row[0], "redirect_uri": row[1], "code_challenge": row[2], "scope": row[3]}

    def issue_tokens(self, client_id, scope):
        access = "at-" + secrets.token_urlsafe(32)
        refresh = "rt-" + secrets.token_urlsafe(32)
        exp = time.time() + _ACCESS_TTL
        self._db.execute("INSERT INTO tokens VALUES(?,?,?,?,?)", (access, "access", client_id, scope, exp))
        self._db.execute("INSERT INTO tokens VALUES(?,?,?,?,?)", (refresh, "refresh", client_id, scope, None))
        return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
                "expires_in": _ACCESS_TTL, "scope": scope}

    def verify_access(self, token):
        row = self._db.execute(
            "SELECT client_id, scope, access_expires_at FROM tokens WHERE token=? AND kind='access'",
            (token,)).fetchone()
        if not row or (row[2] is not None and row[2] < time.time()):
            return None
        return {"client_id": row[0], "scope": row[1]}

    def rotate_refresh(self, refresh_token):
        row = self._db.execute(
            "SELECT client_id, scope FROM tokens WHERE token=? AND kind='refresh'",
            (refresh_token,)).fetchone()
        if not row:
            return None
        self._db.execute("DELETE FROM tokens WHERE token=?", (refresh_token,))
        return self.issue_tokens(row[0], row[1])

    def revoke(self, token):
        self._db.execute("DELETE FROM tokens WHERE token=?", (token,))

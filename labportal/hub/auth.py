"""Sign-in for the Lab Hub: every page and API needs a session, because the hub can power labs, the NMS and CI.

Users live in ~/.config/lab-hub/auth.json (mode 0600; $LAB_HUB_AUTH_FILE), as salted PBKDF2-SHA256 hashes — never the
password itself — next to the random key that signs session cookies:
  {"secret": "<hex>", "users": {"admin": {"salt": "<hex>", "hash": "<hex>", "iterations": 600000}}}
On the first start, when the file does not exist, it is created with the user admin / password admin; change it with
  lab-hub-passwd [user]          (prompts; also adds a user)
A session is a signed cookie (HMAC-SHA256 over the user and an expiry, SESSION_HOURS long), HttpOnly and SameSite=Strict
so other sites cannot use it. LAB_HUB_AUTH=off turns sign-in off again (only on a trusted, single-user machine)."""
import base64, getpass, hashlib, hmac, json, os, secrets, sys, time
from pathlib import Path

FILE = Path(os.environ.get("LAB_HUB_AUTH_FILE", Path.home() / ".config" / "lab-hub" / "auth.json"))
ENABLED = os.environ.get("LAB_HUB_AUTH", "on") != "off"
COOKIE = "labhub_session"
SESSION_HOURS = float(os.environ.get("LAB_HUB_SESSION_HOURS", "12"))
ITERATIONS = 600_000
DEFAULT_USER, DEFAULT_PASSWORD = "admin", "admin"
_failures = {}                                   # client -> [times of recent failed sign-ins]


def _hash(password, salt, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()


def _save(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, FILE); os.chmod(FILE, 0o600)


def _load():
    if not FILE.exists():
        salt = secrets.token_hex(16)
        _save({"secret": secrets.token_hex(32),
               "users": {DEFAULT_USER: {"salt": salt, "hash": _hash(DEFAULT_PASSWORD, salt), "iterations": ITERATIONS}}})
        print(f"lab-hub: created {FILE} with the default user {DEFAULT_USER!r} — change its password with lab-hub-passwd", flush=True)
    return json.loads(FILE.read_text())


def set_password(user, password):
    data = _load(); salt = secrets.token_hex(16)
    data["users"][user] = {"salt": salt, "hash": _hash(password, salt), "iterations": ITERATIONS}
    _save(data)


def check(user, password):
    """True if the user exists and the password matches (constant-time comparison)."""
    u = _load()["users"].get(user)
    if not u:
        _hash(password, "00" * 16)               # spend the same time either way: no hint whether the user exists
        return False
    return hmac.compare_digest(_hash(password, u["salt"], u.get("iterations", ITERATIONS)), u["hash"])


def throttled(client):
    """After 5 failed sign-ins in 5 minutes from one client, refuse further attempts until the oldest ages out."""
    now = time.time(); recent = [t for t in _failures.get(client, []) if now - t < 300]
    _failures[client] = recent
    return len(recent) >= 5


def failed(client):
    _failures.setdefault(client, []).append(time.time())


def issue(user):
    """A session cookie value: payload.signature, both base64url."""
    payload = base64.urlsafe_b64encode(json.dumps({"u": user, "exp": time.time() + SESSION_HOURS * 3600}).encode()).decode().rstrip("=")
    sig = hmac.new(bytes.fromhex(_load()["secret"]), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify(value):
    """The user of a valid, unexpired session cookie, else None. A user removed from auth.json loses access at once."""
    try:
        payload, sig = value.rsplit(".", 1)
        data = _load()
        good = hmac.new(bytes.fromhex(data["secret"]), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, sig):
            return None
        d = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return d["u"] if d["exp"] > time.time() and d["u"] in data["users"] else None
    except Exception:                                          # noqa: BLE001 — anything malformed is simply not a session
        return None


def cli():
    """lab-hub-passwd [user]: set (or add) a user's password."""
    user = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_USER
    pw = getpass.getpass(f"new password for {user}: ")
    if len(pw) < 4: sys.exit("too short")
    if pw != getpass.getpass("again: "): sys.exit("the two entries differ — nothing changed")
    set_password(user, pw); print(f"password for {user} set in {FILE}")

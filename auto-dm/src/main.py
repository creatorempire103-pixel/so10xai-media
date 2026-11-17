"""
so10xai Instagram Auto-DM
=========================

Comment par keyword (EDITOR, TOKENS, ULTRA, KIT...) aaye  -> commenter ko DM (private reply)
                                                          + comment par public reply.
DM mein keyword aaye                                      -> DM se jawab.

Pure Python standard library (koi pip package nahi), taaki Wasmer Edge par
bina dependency ke chal sake.

Environment variables (Wasmer secrets):
  IG_ACCESS_TOKEN   Instagram long-lived access token (Instagram Login)   [required]
  IG_USER_ID        Aapka Instagram professional account ID              [required]
  IG_APP_SECRET     Meta app ka "Instagram app secret"                   [required]
  VERIFY_TOKEN      Koi bhi random string, webhook verify ke liye         [required]
  IG_USERNAME       default "so10xai" (apne comments ignore karne ke liye)
  GRAPH_HOST        default "graph.instagram.com"
  GRAPH_VERSION     default "v23.0"
  RULES_JSON        rules.json ki jagah poora JSON env se dena ho to
  PUBLIC_REPLY      "1" (default) = comment par public reply bhi karo, "0" = sirf DM
  DRY_RUN           "1" = Instagram ko kuch mat bhejo, sirf log karo (testing)
  PORT              default 8080
"""

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def env(name, default=""):
    return os.environ.get(name, default).strip()


ACCESS_TOKEN = env("IG_ACCESS_TOKEN")
IG_USER_ID = env("IG_USER_ID")
APP_SECRET = env("IG_APP_SECRET")
VERIFY_TOKEN = env("VERIFY_TOKEN")
IG_USERNAME = env("IG_USERNAME", "so10xai").lower()
GRAPH_HOST = env("GRAPH_HOST", "graph.instagram.com")
GRAPH_VERSION = env("GRAPH_VERSION", "v23.0")
PUBLIC_REPLY = env("PUBLIC_REPLY", "1") != "0"
DRY_RUN = env("DRY_RUN", "0") == "1"


def log(*parts):
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), *parts, flush=True)


# ---------------------------------------------------------------- rules ----

def load_rules():
    raw = env("RULES_JSON")
    if not raw:
        with open(os.path.join(HERE, "rules.json"), encoding="utf-8") as f:
            raw = f.read()
    data = json.loads(raw)
    rules = []
    for r in data["rules"]:
        kw = r["keyword"].strip()
        rules.append({
            "keyword": kw,
            "pattern": re.compile(r"(?<![\w])" + re.escape(kw) + r"(?![\w])", re.IGNORECASE),
            "dm": r["dm"],
            "public_replies": r.get("public_replies") or [],
        })
    return rules


RULES = load_rules()
_rotation = {}
_lock = threading.Lock()


def match_rule(text):
    """Pehla rule jiska keyword text mein alag shabd ki tarah ho."""
    if not text:
        return None
    for rule in RULES:
        if rule["pattern"].search(text):
            return rule
    return None


def next_public_reply(rule):
    options = rule["public_replies"]
    if not options:
        return None
    with _lock:
        i = _rotation.get(rule["keyword"], 0)
        _rotation[rule["keyword"]] = i + 1
    return options[i % len(options)]


class SeenSet:
    """Ek hi comment/message par do baar reply na ho (Meta webhooks retry karta hai)."""

    def __init__(self, limit=5000):
        self.limit = limit
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def add_if_new(self, key):
        with self.lock:
            if key in self.items:
                return False
            self.items[key] = True
            while len(self.items) > self.limit:
                self.items.popitem(last=False)
            return True


SEEN = SeenSet()


# ------------------------------------------------------------ graph api ----

def graph_post(path, payload):
    url = f"https://{GRAPH_HOST}/{GRAPH_VERSION}/{path}"
    if DRY_RUN:
        log("DRY_RUN POST", url, json.dumps(payload, ensure_ascii=False))
        return {"dry_run": True}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ACCESS_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        log("GRAPH ERROR", e.code, path, detail[:500])
        return None
    except Exception as e:  # network etc.
        log("GRAPH ERROR", path, repr(e))
        return None


def send_private_reply(comment_id, text):
    # https://developers.facebook.com/docs/instagram-platform/private-replies/
    return graph_post(f"{IG_USER_ID}/messages", {
        "recipient": {"comment_id": comment_id},
        "message": {"text": text},
    })


def send_dm(user_id, text):
    return graph_post(f"{IG_USER_ID}/messages", {
        "recipient": {"id": user_id},
        "message": {"text": text},
    })


def reply_to_comment(comment_id, text):
    return graph_post(f"{comment_id}/replies", {"message": text})


# ------------------------------------------------------------- handlers ----

def handle_comment(value):
    comment_id = value.get("id")
    text = value.get("text", "")
    sender = value.get("from") or {}
    username = (sender.get("username") or "").lower()

    if not comment_id:
        return "skip:no-id"
    if sender.get("id") == IG_USER_ID or username == IG_USERNAME:
        return "skip:own"
    if value.get("parent_id"):
        return "skip:reply-comment"
    rule = match_rule(text)
    if not rule:
        return "skip:no-keyword"
    if not SEEN.add_if_new("c:" + comment_id):
        return "skip:duplicate"

    dm_ok = send_private_reply(comment_id, rule["dm"]) is not None
    pub_ok = None
    if PUBLIC_REPLY:
        msg = next_public_reply(rule)
        if msg:
            pub_ok = reply_to_comment(comment_id, msg) is not None
    log("COMMENT", comment_id, "@" + username, rule["keyword"], "dm=", dm_ok, "public=", pub_ok)
    return f"replied:{rule['keyword']}"


def handle_message(event):
    sender_id = (event.get("sender") or {}).get("id")
    message = event.get("message") or {}
    mid = message.get("mid") or ""

    if not sender_id or sender_id == IG_USER_ID:
        return "skip:own"
    if message.get("is_echo") or message.get("is_deleted"):
        return "skip:echo"
    rule = match_rule(message.get("text", ""))
    if not rule:
        return "skip:no-keyword"
    if mid and not SEEN.add_if_new("m:" + mid):
        return "skip:duplicate"

    ok = send_dm(sender_id, rule["dm"]) is not None
    log("DM", sender_id, rule["keyword"], "sent=", ok)
    return f"replied:{rule['keyword']}"


def process_payload(payload):
    results = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") in ("comments", "live_comments"):
                results.append(handle_comment(change.get("value") or {}))
        for event in entry.get("messaging", []):
            if "message" in event:
                results.append(handle_message(event))
    return results


def valid_signature(raw_body, header):
    if not APP_SECRET:
        return False
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


# ----------------------------------------------------------------- http ----

class Handler(BaseHTTPRequestHandler):
    server_version = "so10xai-auto-dm/1.0"

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # quieter default logging
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/health"):
            status = {
                "ok": True,
                "configured": bool(ACCESS_TOKEN and IG_USER_ID and APP_SECRET and VERIFY_TOKEN),
                "keywords": [r["keyword"] for r in RULES],
                "public_reply": PUBLIC_REPLY,
                "dry_run": DRY_RUN,
            }
            return self._send(200, json.dumps(status), "application/json")
        if parsed.path == "/webhook":
            q = urllib.parse.parse_qs(parsed.query)
            mode = (q.get("hub.mode") or [""])[0]
            token = (q.get("hub.verify_token") or [""])[0]
            challenge = (q.get("hub.challenge") or [""])[0]
            if mode == "subscribe" and VERIFY_TOKEN and hmac.compare_digest(token, VERIFY_TOKEN):
                log("WEBHOOK verified")
                return self._send(200, challenge)
            return self._send(403, "verification failed")
        return self._send(404, "not found")

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/webhook":
            return self._send(404, "not found")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not valid_signature(raw, self.headers.get("X-Hub-Signature-256")):
            log("WEBHOOK bad signature")
            return self._send(401, "bad signature")
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return self._send(400, "bad json")
        try:
            results = process_payload(payload)
        except Exception as e:  # never make Meta retry because of our bug
            log("PROCESS ERROR", repr(e))
            results = ["error"]
        return self._send(200, json.dumps({"results": results}), "application/json")


def main():
    missing = [n for n, v in [("IG_ACCESS_TOKEN", ACCESS_TOKEN), ("IG_USER_ID", IG_USER_ID),
                              ("IG_APP_SECRET", APP_SECRET), ("VERIFY_TOKEN", VERIFY_TOKEN)] if not v]
    if missing:
        log("WARNING missing env:", ", ".join(missing))
    host = env("HOST", "0.0.0.0")
    port = int(env("PORT", "8080"))
    log(f"auto-dm listening on {host}:{port} keywords={[r['keyword'] for r in RULES]} dry_run={DRY_RUN}")
    HTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())

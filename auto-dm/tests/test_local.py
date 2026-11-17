"""Local test: server ko DRY_RUN mein chala kar fake Meta webhooks bhejta hai.

Run:  python3 auto-dm/tests/test_local.py
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "..", "src", "main.py")
PORT = 8765
SECRET = "test-secret"
BASE = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None, sign=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        if sign:
            sig = hmac.new(SECRET.encode(), data, hashlib.sha256).hexdigest()
            req.add_header("X-Hub-Signature-256", "sha256=" + sig)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def comment(cid, text, username="fan1", uid="111", parent=None):
    value = {"id": cid, "text": text, "from": {"id": uid, "username": username}, "media": {"id": "m1"}}
    if parent:
        value["parent_id"] = parent
    return {"object": "instagram", "entry": [{"id": "999", "time": 1, "changes": [{"field": "comments", "value": value}]}]}


def dm(mid, text, sender="222", echo=False):
    msg = {"mid": mid, "text": text}
    if echo:
        msg["is_echo"] = True
    return {"object": "instagram", "entry": [{"id": "999", "time": 1, "messaging": [
        {"sender": {"id": sender}, "recipient": {"id": "999"}, "timestamp": 1, "message": msg}]}]}


def results(body):
    return json.loads(body)["results"]


def main():
    env = dict(os.environ, DRY_RUN="1", PORT=str(PORT), HOST="127.0.0.1", IG_ACCESS_TOKEN="x",
               IG_USER_ID="999", IG_APP_SECRET=SECRET, VERIFY_TOKEN="verify-me")
    proc = subprocess.Popen([sys.executable, MAIN], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    failures = 0

    def check(name, got, want):
        nonlocal failures
        ok = got == want
        failures += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got={got!r} want={want!r}"))

    try:
        for _ in range(50):
            try:
                call("GET", "/health")
                break
            except Exception:
                time.sleep(0.1)

        s, b = call("GET", "/health")
        check("health", (s, json.loads(b)["configured"]), (200, True))
        check("verify ok", call("GET", "/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=42"), (200, "42"))
        check("verify bad token", call("GET", "/webhook?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=42")[0], 403)
        check("unsigned rejected", call("POST", "/webhook", comment("c0", "EDITOR"), sign=False)[0], 401)

        check("keyword upper", results(call("POST", "/webhook", comment("c1", "EDITOR please"))[1]), ["replied:EDITOR"])
        check("keyword lower+emoji", results(call("POST", "/webhook", comment("c2", "tokens🔥"))[1]), ["replied:TOKENS"])
        check("keyword in word ignored", results(call("POST", "/webhook", comment("c3", "ultraviolet vibes"))[1]), ["skip:no-keyword"])
        check("KIT not in KITCHEN", results(call("POST", "/webhook", comment("c4", "my kitchen"))[1]), ["skip:no-keyword"])
        check("duplicate retry", results(call("POST", "/webhook", comment("c1", "EDITOR please"))[1]), ["skip:duplicate"])
        check("own comment", results(call("POST", "/webhook", comment("c5", "EDITOR", username="so10xai", uid="999"))[1]), ["skip:own"])
        check("reply-thread skipped", results(call("POST", "/webhook", comment("c6", "EDITOR", parent="c1"))[1]), ["skip:reply-comment"])
        check("dm keyword", results(call("POST", "/webhook", dm("m1", "kit"))[1]), ["replied:KIT"])
        check("dm echo ignored", results(call("POST", "/webhook", dm("m2", "KIT", echo=True))[1]), ["skip:echo"])
        check("dm no keyword", results(call("POST", "/webhook", dm("m3", "hello bro"))[1]), ["skip:no-keyword"])
        check("rotation 2nd EDITOR", results(call("POST", "/webhook", comment("c7", "Editor"))[1]), ["replied:EDITOR"])
    finally:
        proc.terminate()
        out = proc.communicate(timeout=5)[0]
        print("\n--- server log ---\n" + out)

    print("ALL PASSED" if failures == 0 else f"{failures} FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

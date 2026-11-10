"""Official Sign in with ChatGPT for a local, text-only Controller."""
import base64
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
from hashlib import sha256
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import re
import secrets
import ssl
from tempfile import NamedTemporaryFile
from time import monotonic, time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from .lease import Blocked
from .ledger import canonical, check_path

AUTH = "auth.openai.com"
API = "api.openai.com"
ISSUER = f"https://{AUTH}"
RESOURCE = f"https://{API}/v1"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"
TOKEN = "/api/accounts/oauth/token"
CALLBACK = "/auth/callback"
LIMIT = 2 ** 20
ERRORS = {
    "invalid_grant", "invalid_client", "access_denied", "invalid_token",
    "subscription_sharing_user_not_eligible", "subscription_sharing_usage_limit_exceeded",
    "subscription_sharing_usage_unavailable", "subscription_sharing_unsupported_capability",
    "subscription_sharing_unavailable", "subscription_sharing_not_authorized",
}


def _error_code(value):
    if isinstance(value, dict):
        value = value.get("code", value.get("type"))
    return value if isinstance(value, str) and value in ERRORS else "UNRECOGNIZED_ERROR"


class SubscriptionError(Blocked):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(f"ChatGPT subscription HTTP {status}: {code}")


@contextmanager
def _request(host, path, method="GET", body=None, *, token=None, form=False, timeout=8):
    # HTTPSConnection uses the system trust store. No environment proxy,
    # automatic redirect, arbitrary destination or API-key fallback.
    if (host, path) not in {
        (AUTH, TOKEN), (AUTH, "/.well-known/openid-configuration"),
        (AUTH, "/.well-known/jwks.json"), (AUTH, "/api/accounts/oauth/revoke"),
        (API, "/v1/models"), (API, "/v1/responses"),
    }:
        raise Blocked("Subscription endpoint outside the official allowlist")
    headers = {"Accept": "text/event-stream" if path == "/v1/responses" else "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        body = (urlencode(body) if form else canonical(body)).encode("utf-8")
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.load_default_certs()
    connection = http.client.HTTPSConnection(host, timeout=timeout, context=tls)
    try:
        connection.request(method, path, body, headers)
        with connection.getresponse() as response:
            if response.status != 200:
                try:
                    detail = json.loads(response.read(8192))
                    code = _error_code(detail.get("error") if isinstance(detail, dict) else None)
                except (ValueError, UnicodeError):
                    code = "UNRECOGNIZED_ERROR"
                raise SubscriptionError(response.status, code)
            yield response
    except TimeoutError:
        raise Blocked("ChatGPT subscription network timeout") from None
    except (OSError, http.client.HTTPException):
        raise Blocked("ChatGPT subscription connection interrupted") from None
    finally:
        connection.close()


def _json(host, path, *, body=None, token=None):
    with _request(host, path, "POST" if body is not None else "GET", body,
                  token=token, form=host == AUTH) as response:
        raw = response.read(LIMIT + 1)
    try:
        value = json.loads(raw) if len(raw) <= LIMIT else None
    except (ValueError, UnicodeError, RecursionError):
        value = None
    if type(value) is not dict:
        raise Blocked("Invalid ChatGPT subscription response")
    return value


def _b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unbase(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _identity(token, client, nonce):
    """Verify RS256 with the pinned issuer's published RSA signing keys."""
    try:
        if type(token) is not str or len(token) > 16384:
            raise ValueError
        header, payload, signature = token.split(".")
        algorithm = json.loads(_unbase(header))
        claims = json.loads(_unbase(payload))
        if (algorithm["alg"] != "RS256" or "crit" in algorithm or not algorithm.get("kid")
                or claims["iss"] != ISSUER or claims["aud"] not in (client, [client])
                or claims.get("azp", client) != client or claims["nonce"] != nonce
                or type(claims["exp"]) is not int or claims["exp"] <= time()
                or type(claims["sub"]) is not str or not claims["sub"]):
            raise ValueError
        metadata = _json(AUTH, "/.well-known/openid-configuration")
        if (metadata.get("issuer"), metadata.get("jwks_uri")) != (ISSUER, ISSUER + "/.well-known/jwks.json"):
            raise ValueError
        key, = (item for item in _json(AUTH, "/.well-known/jwks.json")["keys"]
                if item.get("kid") == algorithm["kid"] and item.get("kty") == "RSA"
                and item.get("use", "sig") == "sig" and item.get("alg", "RS256") == "RS256")
        modulus = int.from_bytes(_unbase(key["n"]), "big")
        exponent = int.from_bytes(_unbase(key["e"]), "big")
        signed = _unbase(signature)
        width = (modulus.bit_length() + 7) // 8
        signature_int = int.from_bytes(signed, "big")
        if not 256 <= width <= 1024 or not 3 <= exponent <= 2**32 or len(signed) != width or signature_int >= modulus:
            raise ValueError
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420")
        digest_info += sha256(f"{header}.{payload}".encode("ascii")).digest()
        expected = b"\x00\x01" + b"\xff" * (width - len(digest_info) - 3) + b"\x00" + digest_info
        if not secrets.compare_digest(pow(signature_int, exponent, modulus).to_bytes(width, "big"), expected):
            raise ValueError
        return claims["sub"]
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, RecursionError):
        raise Blocked("ChatGPT ID token verification failed") from None


def _protect(raw, decrypt=False):
    """Current-user DPAPI with UI disabled. The protected blob is never logged."""
    class Blob(c.Structure):
        _fields_ = [("size", w.DWORD), ("data", c.c_void_p)]

    crypt = c.WinDLL("crypt32", winmode=0x800, use_last_error=True)
    free = c.WinDLL("kernel32", winmode=0x800).LocalFree
    free.argtypes = [c.c_void_p]
    free.restype = c.c_void_p
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [c.POINTER(Blob), c.c_void_p, c.c_void_p, c.c_void_p, c.c_void_p,
                          w.DWORD, c.POINTER(Blob)]
    operation.restype = w.BOOL
    buffer = c.create_string_buffer(raw)
    source, output = Blob(len(raw), c.addressof(buffer)), Blob()
    try:
        if not operation(c.byref(source), None, None, None, None, 1, c.byref(output)):
            raise Blocked("Windows credential protection failed")
        return c.string_at(output.data, output.size)
    finally:
        c.memset(buffer, 0, len(raw))
        if output.data:
            c.memset(output.data, 0, output.size)
            free(output.data)


class AccountStore:
    """Keep issued clients separate. Callers serialize changes with the Controller slot."""
    def __init__(self, ledger):
        self.path = ledger.local_directory("state") / "chatgpt-subscription.dpapi"

    def read(self):
        check_path(self.path)
        if not self.path.exists():
            return {"host": None, "accounts": {}}
        return read_protected(self.path)

    def save(self, state):
        write_protected(self.path, state)

    def accounts(self):
        return [{"account": alias, "signed_in": "access_token" in record,
                 "subscription_granted": PLAN_SCOPE in record.get("scopes", ())}
                for alias, record in self.read()["accounts"].items()]

    def login(self, open_browser, alias=None, cancelled=lambda: False):
        saved = self.read()
        old = saved["accounts"].get(alias)
        if alias is not None and old is None:
            raise Blocked("Unknown ChatGPT account label")
        if not saved["host"]:
            saved["host"] = f"urn:uuid:{uuid4()}"
            self.save(saved)
        code, client, verifier, nonce, redirect = _authorize(open_browser, saved["host"], old, cancelled)
        tokens = _json(AUTH, TOKEN, body={"grant_type": "authorization_code", "client_id": client, "code": code,
                                        "code_verifier": verifier, "redirect_uri": redirect, "resource": RESOURCE})
        subject = _identity(tokens.get("id_token"), client, nonce)
        if old and old["subject"] != subject:
            raise Blocked("ChatGPT account does not match the existing registration")
        if not old and any(r["client_id"] == client for r in saved["accounts"].values()):
            raise Blocked("Use the existing ChatGPT account registration")
        alias = alias or uuid4().hex[:12]
        saved["accounts"][alias] = {"client_id": client, "subject": subject,
                                    **_credentials(tokens), "id_token": tokens["id_token"]}
        self.save(saved)
        return {"account": alias, "subscription_granted": PLAN_SCOPE in saved["accounts"][alias]["scopes"]}

    def access(self, alias):
        saved = self.read()
        record = saved["accounts"].get(alias, {})
        if PLAN_SCOPE not in record.get("scopes", ()):
            raise Blocked("ChatGPT plan use is not granted for this registration")
        if record["expires"] > time() + 60:
            return record["access_token"]
        if not record.get("refresh_token"):
            raise Blocked("ChatGPT session expired; reauthorize this registration")
        try:
            tokens = _json(AUTH, TOKEN, body={"grant_type": "refresh_token", "client_id": record["client_id"],
                                            "refresh_token": record["refresh_token"], "resource": RESOURCE})
        except SubscriptionError as error:
            if error.code in {"invalid_grant", "invalid_token"}:
                self._clear(saved, alias)
            raise
        record.update(_credentials(tokens, record))
        self.save(saved)
        if PLAN_SCOPE not in record["scopes"]:
            raise Blocked("ChatGPT plan permission was removed")
        return record["access_token"]

    def _clear(self, saved, alias):
        record = saved["accounts"][alias]
        saved["accounts"][alias] = {key: record[key] for key in ("client_id", "subject")}
        self.save(saved)

    def invalidate(self, alias):
        saved = self.read()
        if alias in saved["accounts"]:
            self._clear(saved, alias)

    def logout(self, alias):
        saved = self.read()
        if alias not in saved["accounts"]:
            raise Blocked("Unknown ChatGPT account label")
        record = saved["accounts"][alias]
        revoked = False
        if refresh := record.get("refresh_token"):
            try:
                metadata = _json(AUTH, "/.well-known/openid-configuration")
                path = "/api/accounts/oauth/revoke"
                if metadata.get("revocation_endpoint") != ISSUER + path:
                    raise Blocked("Invalid ChatGPT revocation endpoint")
                with _request(AUTH, path, "POST", {"token": refresh, "token_type_hint": "refresh_token",
                                                   "client_id": record["client_id"]}, form=True):
                    revoked = True
            except Blocked:
                pass
        self._clear(saved, alias)
        return {"signed_out": True, "remote_revocation_confirmed": revoked}


def _credentials(tokens, old=None):
    old = old or {}
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token", old.get("refresh_token"))
    seconds = tokens.get("expires_in")
    scope = tokens.get("scope")
    token_type = tokens.get("token_type")
    if (not isinstance(token_type, str) or token_type.lower() != "bearer"
            or type(seconds) is not int or not 0 < seconds <= 86400
            or not isinstance(access, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", access)
            or (refresh is not None and (not isinstance(refresh, str) or not refresh or len(refresh) > 16384))
            or (scope is not None and not isinstance(scope, str))):
        raise Blocked("ChatGPT token response is invalid")
    return {"access_token": access, "refresh_token": refresh, "expires": int(time()) + seconds,
            "scopes": scope.split() if scope is not None else old.get("scopes", [])}


def write_protected(path, value):
    check_path(path)
    encrypted = _protect(canonical(value).encode("utf-8"))
    if len(encrypted) > LIMIT:
        raise Blocked("ChatGPT protected file limit exceeded")
    with NamedTemporaryFile(dir=path.parent, prefix="siwc-", delete=False) as stream:
        temporary = stream.name
        try:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.replace(temporary, path)
        finally:
            stream.close()
            if os.path.exists(temporary):
                os.unlink(temporary)


def read_protected(path):
    check_path(path)
    with path.open("rb") as stream:
        blob = stream.read(LIMIT + 1)
    if not 0 < len(blob) <= LIMIT:
        raise Blocked("ChatGPT protected file is invalid")
    try:
        return json.loads(_protect(blob, decrypt=True))
    except (ValueError, UnicodeError):
        raise Blocked("ChatGPT protected file is invalid") from None


def _authorize(open_browser, host, old, cancelled):
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    result = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        timeout = 2

        def log_message(self, *_args):
            pass  # The query contains the authorization code.

        def do_GET(self):
            try:
                location = urlsplit(self.path)
                query = parse_qs(location.query, max_num_fields=20)
            except ValueError:
                self.send_error(400)
                return
            valid = (location.path == CALLBACK and self.headers.get("Host") == f"127.0.0.1:{port}"
                     and query.get("state") == [state])
            if valid:
                result.update(query)
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Return to DevOrchestra for sign-in status." if valid else b"Invalid callback.")

    class LocalCallback(HTTPServer):
        allow_reuse_address = False

        def handle_error(self, *_args):
            pass

    with LocalCallback(("127.0.0.1", 0), CallbackHandler) as listener:
        listener.timeout = 0.5
        port = listener.server_port
        redirect = f"http://127.0.0.1:{port}{CALLBACK}"
        client = old["client_id"] if old else "dynamic_agent_client"
        query = {"client_id": client, "ext_agent_host_id": host, "response_type": "code", "redirect_uri": redirect,
                 "scope": SCOPES, "resource": RESOURCE, "state": state, "nonce": nonce,
                 "code_challenge_method": "S256", "code_challenge": _b64url(sha256(verifier.encode()).digest())}
        if not old:
            query["agent_name_hint"] = "DevOrchestra"
        elif old.get("id_token"):
            query["id_token_hint"] = old["id_token"]
        if not cancelled():
            open_browser(f"{ISSUER}/api/accounts/authorize?{urlencode(query)}")
        deadline = monotonic() + 180
        while not result and monotonic() < deadline and not cancelled():
            listener.handle_request()
    if cancelled():
        raise Blocked("ChatGPT browser consent cancelled")
    if not result:
        raise Blocked("ChatGPT browser consent timed out")
    if "error" in result:
        raise Blocked(f"ChatGPT authorization ended: {_error_code(result['error'][0])}")
    issued = result.get("client_id", [client] if old else [])
    codes = result.get("code", [])
    if (len(issued) != 1 or not re.fullmatch(r"oaiapp_[A-Za-z0-9_-]{1,180}", issued[0])
            or (old and issued[0] != client) or len(codes) != 1 or not 0 < len(codes[0]) <= 4096):
        raise Blocked("ChatGPT authorization callback is invalid")
    return codes[0], issued[0], verifier, nonce, redirect


def list_models(token):
    models = _json(API, "/v1/models", token=token).get("models")
    if type(models) is not list:
        raise Blocked("ChatGPT account model catalog is invalid")
    return [{"model": item["slug"], "name": item.get("display_name", item["slug"])}
            for item in models if isinstance(item, dict) and item.get("visibility") == "list"
            and isinstance(item.get("slug"), str)]


def _events(response, check, *, verify_framing=False):
    buffer, lines, received = b"", [], 0
    while True:
        check()
        data = response.read1(8192)
        if not data:
            return
        received += len(data)
        if received > LIMIT:
            raise Blocked("ChatGPT response exceeded the stream limit")
        buffer += data
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.removesuffix(b"\r")
            if line.startswith(b"data:"):
                lines.append(line[5:].lstrip(b" "))
            elif not line and lines:
                try:
                    yield json.loads(b"\n".join(lines))
                except (ValueError, UnicodeError, RecursionError):
                    raise Blocked("ChatGPT response stream contains invalid JSON") from None
                lines.clear()
            elif (verify_framing and line
                  and not line.startswith((b":", b"event:", b"id:", b"retry:"))):
                raise Blocked("ChatGPT HTTP 200 text/plain body is not framed as SSE")


def complete(token, body, check, before_send, deadline):
    """Read a bounded text response; deltas alone never settle a turn."""
    if (set(body) != {"model", "store", "stream", "tools", "instructions", "input"}
            or body["store"] is not False or body["stream"] is not True or body["tools"] != []):
        raise Blocked("Subscription Manager transport requires a tool-free text stream")
    check()
    before_send()
    with _request(API, "/v1/responses", "POST", body, token=token,
                  timeout=max(0.1, min(8, deadline - monotonic()))) as response:
        media = response.headers.get_content_type()
        if media not in {"text/event-stream", "text/plain"}:
            if media == "application/json":
                try:
                    payload = json.loads(response.read(8192))
                    if isinstance(payload, dict) and "error" in payload:
                        raise SubscriptionError(response.status, _error_code(payload["error"]))
                except (ValueError, UnicodeError, RecursionError):
                    pass
            category = media if media in {"application/json", "text/html", "text/plain",
                                          "application/octet-stream"} else "other-or-missing"
            raise Blocked(f"ChatGPT HTTP {response.status} returned {category} instead of an event stream")
        for event in _events(response, check, verify_framing=media == "text/plain"):
            if not isinstance(event, dict):
                raise Blocked("ChatGPT response event is invalid")
            kind = event.get("type")
            if kind in {"response.failed", "response.incomplete", "error"}:
                detail = event.get("response", {})
                error = detail.get("error") if isinstance(detail, dict) else None
                code = _error_code(error or event)
                if code == "subscription_sharing_usage_limit_exceeded":
                    raise SubscriptionError(response.status, code)
                raise Blocked(f"ChatGPT terminal event {kind}: {code}")
            if kind == "response.completed":
                check()
                result = event.get("response")
                if not isinstance(result, dict) or result.get("status") != "completed":
                    raise Blocked("ChatGPT completion status is invalid")
                return result
    raise Blocked("ChatGPT stream ended before response.completed")

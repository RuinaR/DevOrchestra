"""Provision owner-pinned portable tools for an offline Developer role."""
from hashlib import sha256
from pathlib import Path, PureWindowsPath
import re
import stat
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from zipfile import BadZipFile, ZipFile

from .lease import Blocked
from .ledger import check_path


PREFIX = ".devorchestra-tools-"
MAX_TOOLS = 4
MAX_DOWNLOAD = 256 * 1024**2
MAX_EXPANDED = 512 * 1024**2


def manifest(value):
    if type(value) is not list or not 1 <= len(value) <= MAX_TOOLS:
        raise Blocked(f"승인할 포터블 ZIP을 1~{MAX_TOOLS}개 입력하세요.")
    names = set()
    for tool in value:
        if (type(tool) is not dict or tool.keys() != {"name", "url", "sha256"}
                or any(type(field) is not str for field in tool.values())
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", tool["name"])
                or PureWindowsPath(tool["name"]).is_reserved()
                or tool["name"] in names or not re.fullmatch(r"[a-f0-9]{64}", tool["sha256"])):
            raise Blocked("도구마다 고유한 name과 소문자 SHA-256을 입력하세요.")
        url = tool["url"]
        try:
            parts = urlsplit(url)
            valid = (len(url) <= 1024 and url.isascii() and not re.search(r"[\x00-\x20\x7f\\]", url)
                     and parts.scheme == "https" and not parts.query and not parts.fragment
                     and re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*", parts.netloc)
                     and parts.path.lower().endswith(".zip"))
        except ValueError:
            valid = False
        if not valid:
            raise Blocked("인증이나 리디렉션이 필요 없는 직접 HTTPS ZIP 주소를 사용하세요.")
        names.add(tool["name"])
    return sorted(value, key=lambda item: item["name"])


class _DirectDownload(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Blocked("승인된 ZIP 주소가 다른 주소로 리디렉션했습니다.")


def download(tool, path, check):
    check_path(path)
    deadline = monotonic() + 180
    checksum = sha256()
    size = 0
    check()
    opener = build_opener(ProxyHandler({}), _DirectDownload())
    with opener.open(tool["url"], timeout=8) as response, path.open("xb") as stream:
        if response.status != 200:
            raise Blocked("프로젝트 도구 ZIP 응답이 올바르지 않습니다.")
        while True:
            check()
            if monotonic() >= deadline:
                raise Blocked("프로젝트 도구 다운로드 시간 초과")
            chunk = response.read1(1024**2)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise Blocked("프로젝트 도구 ZIP 다운로드 크기 초과")
            checksum.update(chunk)
            stream.write(chunk)
    if checksum.hexdigest() != tool["sha256"]:
        raise Blocked("프로젝트 도구 ZIP SHA-256 불일치")
    return size


def _member_path(name):
    parts = name.removesuffix("/").split("/")
    if (len(name) > 180 or any(
            not part or part in {".", ".."} or part.endswith((".", " "))
            or re.search(r'[\x00-\x1f\x7f<>:"|?*\\]', part)
            or PureWindowsPath(part).is_reserved() or part.casefold() in {".git", ".ai-local"}
            for part in parts)):
        raise Blocked("프로젝트 도구 ZIP에 안전하지 않은 경로가 있습니다.")
    return Path(*parts)


def extract(archive, root, check):
    """Extract regular files only, with Windows path and expansion bounds."""
    check_path(archive)
    check_path(root)
    try:
        with ZipFile(archive) as package:
            members = package.infolist()
            if not members or len(members) > 4096 or sum(item.file_size for item in members) > MAX_EXPANDED:
                raise Blocked("프로젝트 도구 ZIP 압축 해제 크기 또는 항목 수 초과")
            seen = set()
            for item in members:
                check()
                relative = _member_path(item.orig_filename)
                key = str(relative).casefold()
                kind = stat.S_IFMT(item.external_attr >> 16)
                if (key in seen or kind not in (0, stat.S_IFDIR if item.is_dir() else stat.S_IFREG)
                        or item.external_attr & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    raise Blocked("프로젝트 도구 ZIP에 중복 경로 또는 특수 파일이 있습니다.")
                seen.add(key)
                target = root / relative
                check_path(target)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, target.open("xb") as destination:
                    while chunk := source.read(1024**2):
                        check()
                        destination.write(chunk)
    except (BadZipFile, RuntimeError, NotImplementedError) as error:
        if isinstance(error, Blocked):
            raise
        raise Blocked("프로젝트 도구 ZIP을 검증하거나 풀 수 없습니다.") from error

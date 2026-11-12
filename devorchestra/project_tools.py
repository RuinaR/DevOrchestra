"""Provision owner-pinned portable tools for an offline Developer role."""
from hashlib import sha256
import json
from pathlib import Path, PureWindowsPath
import re
import stat
from time import monotonic
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zipfile import BadZipFile, ZipFile

from .lease import Blocked
from .ledger import check_path


PREFIX = ".devorchestra-tools-"
MAX_TOOLS = 4
MAX_DOWNLOAD = 256 * 1024**2
MAX_EXPANDED = 512 * 1024**2

GITHUB_POLICY = "GITHUB_PUBLIC_ZIP_V1"
_REPOSITORY = r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}"
_TAG = r"[A-Za-z0-9_][A-Za-z0-9_./+-]{0,99}"
_ASSET = r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,119}\.zip"


class PackageUnavailable(Blocked):
    """GitHub could not provide the approved package; no Developer was dispatched."""


def selection(requested):
    if type(requested) is not list or len(requested) > MAX_TOOLS:
        raise Blocked(f"한 작업에서 GitHub 자료를 최대 {MAX_TOOLS}개 지정하세요.")
    seen = set()
    for item in requested:
        if (type(item) is not dict or item.keys() != {"repository", "ref", "asset"}
                or any(type(field) is not str for field in item.values())
                or not re.fullmatch(_REPOSITORY, item["repository"])
                or any(part in {".", "..", ""} for part in item["ref"].split("/"))
                or not (re.fullmatch(r"[0-9a-f]{40}", item["ref"]) if not item["asset"] else
                        re.fullmatch(_TAG, item["ref"]) and re.fullmatch(_ASSET, item["asset"]))):
            raise Blocked("GitHub 저장소, 릴리스 태그와 ZIP 이름 또는 전체 커밋 SHA를 지정하세요.")
        key = (item["repository"].casefold(), item["ref"], item["asset"])
        if key in seen:
            raise Blocked("같은 GitHub 자료를 중복 지정할 수 없습니다.")
        seen.add(key)
    return requested


def _github_metadata(path, check):
    check()
    opener = build_opener(ProxyHandler({}), _DirectDownload())
    request = Request("https://api.github.com/" + path, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "DevOrchestra",
    })
    try:
        with opener.open(request, timeout=8) as response:
            raw = response.read(1024**2 + 1)
    except (URLError, TimeoutError) as error:
        raise PackageUnavailable("GitHub 자료 조회에 실패했습니다. 공개 여부와 정확한 버전을 확인하세요.") from error
    check()
    if len(raw) > 1024**2:
        raise Blocked("GitHub 응답이 크기 한도를 넘었습니다.")
    value = json.loads(raw)
    if type(value) is not dict:
        raise Blocked("GitHub 자료의 메타데이터 형식을 확인할 수 없습니다.")
    return value


def github_package(item, check):
    selection([item])
    repository, ref, asset = (item[key] for key in ("repository", "ref", "asset"))
    if not asset:
        result = _github_metadata(f"repos/{repository}/commits/{ref}", check)
        if result.get("sha") != ref:
            raise PackageUnavailable("GitHub가 선택한 소스 커밋을 확인하지 못했습니다.")
        return {"url": f"https://codeload.github.com/{repository}/zip/{ref}", "sha256": None}
    tag = quote(ref, safe="")
    result = _github_metadata(f"repos/{repository}/releases/tags/{tag}", check)
    if result.get("tag_name") != ref or result.get("draft") is not False:
        raise PackageUnavailable("GitHub에서 선택한 공개 릴리스를 확인하지 못했습니다.")
    row = next((value for value in result.get("assets", []) if type(value) is dict
                and value.get("name") == asset and value.get("state") == "uploaded"), {})
    checksum = row.get("digest")
    if (type(checksum) is not str or not re.fullmatch(r"sha256:[a-f0-9]{64}", checksum)
            or type(row.get("size")) is not int or not 0 < row["size"] <= MAX_DOWNLOAD):
        raise PackageUnavailable("GitHub 릴리스 ZIP의 게시 SHA-256이나 크기를 확인할 수 없습니다.")
    expected = f"https://github.com/{repository}/releases/download/{tag}/{quote(asset, safe='')}"
    if row.get("browser_download_url", "").casefold() != expected.casefold():
        raise Blocked("GitHub 릴리스의 다운로드 주소가 선택한 저장소와 일치하지 않습니다.")
    return {"url": expected, "sha256": checksum[7:], "github_release": True}


def manifest(value):
    if type(value) is not list or not 1 <= len(value) <= MAX_TOOLS:
        raise Blocked(f"승인할 포터블 ZIP을 1~{MAX_TOOLS}개 입력하세요.")
    names = set()
    for tool in value:
        if (type(tool) is not dict or tool.keys() != {"name", "url", "sha256"}
                or any(type(field) is not str for field in tool.values())
                or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,39}", tool["name"]) or tool["name"].endswith(".")
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


class _GitHubReleaseDownload(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (urlsplit(req.full_url).netloc != "github.com" or target.scheme != "https"
                or target.netloc != "release-assets.githubusercontent.com"):
            raise Blocked("GitHub 릴리스가 허용된 자산 서버 이외의 주소로 이동했습니다.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(tool, path, check):
    check_path(path)
    deadline = monotonic() + 180
    checksum = sha256()
    size = 0
    check()
    redirect = _GitHubReleaseDownload() if tool.get("github_release") else _DirectDownload()
    opener = build_opener(ProxyHandler({}), redirect)
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
    actual = checksum.hexdigest()
    if tool["sha256"] is not None and actual != tool["sha256"]:
        raise Blocked("프로젝트 도구 ZIP SHA-256 불일치")
    return {"bytes": size, "sha256": actual}


def download(tool, path, check):
    return _download(tool, path, check)["bytes"]


def download_github(tool, path, check):
    try:
        return _download(tool, path, check)
    except (URLError, TimeoutError) as error:
        raise PackageUnavailable("GitHub ZIP을 내려받을 수 없습니다.") from error


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

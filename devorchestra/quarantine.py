"""P2.1 in-memory bundle checks. No extraction, guest transport or project writes."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import re
import stat
from zipfile import BadZipFile, ZIP_STORED, ZipFile

from .lease import Blocked

MAX_FILES = 16
MAX_FILE_BYTES = 64 * 1024
MAX_BUNDLE_BYTES = MAX_FILES * MAX_FILE_BYTES + 16 * 1024
COMPONENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)),
           *(f"lpt{i}" for i in range(10))}


@dataclass(frozen=True)
class FileSpec:
    path: str
    size: int
    sha256: str


def _allowed(path):
    parts = path.split("/")
    if len(parts) > 4 or not parts[-1].endswith(".txt"):
        return False
    stems = (*parts[:-1], parts[-1][:-4])
    return all(COMPONENT.fullmatch(part) and part not in DEVICES for part in stems)


def _check_payload(spec, data):
    if len(data) != spec.size or sha256(data).hexdigest() != spec.sha256:
        raise Blocked("Bundle size or hash mismatch")
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Blocked("Bundle payload is not UTF-8 text") from error
    if any(ord(char) < 32 and char not in "\r\n\t" for char in decoded):
        raise Blocked("Bundle payload contains control characters")


def _manifest(entries):
    if not 1 <= len(entries) <= MAX_FILES:
        raise Blocked("Bundle file count out of range")
    expected = {}
    for spec in entries:
        if (not _allowed(spec.path) or spec.path in expected
                or not 0 <= spec.size <= MAX_FILE_BYTES
                or not re.fullmatch("[0-9a-f]{64}", spec.sha256)):
            raise Blocked("Invalid or duplicate bundle manifest entry")
        expected[spec.path] = spec
    return expected


def package_inputs(entries, approved):
    """Package explicitly supplied, host-approved synthetic text bytes."""
    expected = _manifest(approved)
    if set(entries) != set(expected):
        raise Blocked("Input names differ from the approved manifest")
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, spec in sorted(expected.items()):
            data = entries[name]
            _check_payload(spec, data)
            archive.writestr(name, data)
    return output.getvalue()


def inspect_bundle(bundle: bytes, approved: tuple[FileSpec, ...]):
    """Return checked immutable bytes for review, never publish or unpack them.

    The trusted caller must establish guest shutdown and transport provenance
    before collecting a bundle. A guest-supplied hash is not host approval.
    """
    expected = _manifest(approved)
    if type(bundle) is not bytes or len(bundle) > MAX_BUNDLE_BYTES:
        raise Blocked("Bundle byte limit exceeded")
    try:
        with ZipFile(BytesIO(bundle)) as archive:
            members = archive.infolist()
            if len(members) != len(expected) or {m.filename for m in members} != set(expected):
                raise Blocked("Bundle inventory differs from the approved manifest")
            result = []
            for member in members:
                spec = expected[member.filename]
                kind = stat.S_IFMT(member.external_attr >> 16)
                if (member.orig_filename != member.filename or kind not in (0, stat.S_IFREG)
                        or member.external_attr & 0x410  # directory or reparse point
                        or member.flag_bits & 1 or member.compress_type != ZIP_STORED
                        or member.file_size != spec.size or member.compress_size != spec.size):
                    raise Blocked("Unsupported bundle member")
                with archive.open(member) as stream:
                    data = stream.read(spec.size + 1)
                _check_payload(spec, data)
                result.append((spec.path, data))
            return tuple(result)
    except (BadZipFile, EOFError, NotImplementedError, RuntimeError) as error:
        if isinstance(error, Blocked):
            raise
        raise Blocked("Malformed bundle") from error
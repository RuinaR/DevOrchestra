"""Fixed CONNECT relay for the native egress trial's owned loopback sockets."""

from ipaddress import ip_address
from select import select
from socket import AF_INET, AF_INET6, SHUT_WR, SOCK_STREAM, SOL_SOCKET, SO_ACCEPTCONN, socket
from time import monotonic


AUTHORITY = b"devorchestra-egress.invalid:443"
REQUEST = b"CONNECT " + AUTHORITY + b" HTTP/1.1\r\nHost: " + AUTHORITY + b"\r\n\r\n"
ACCEPTED = b"HTTP/1.1 200 Connection Established\r\n\r\n"
DENIED = b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"


def _endpoint(listener):
    address = listener.getsockname()
    if (listener.family not in (AF_INET, AF_INET6) or listener.type != SOCK_STREAM
            or not listener.getsockopt(SOL_SOCKET, SO_ACCEPTCONN)
            or not ip_address(address[0]).is_loopback or not address[1]):
        raise ValueError("The trial requires a listening TCP loopback socket")
    return address


def _receive_request(client, deadline):
    # Read exactly through the header terminator, without consuming tunnel bytes.
    request = bytearray()
    while len(request) < 1024:
        client.settimeout(max(0, deadline - monotonic()))
        byte = client.recv(1)
        if not byte:
            return False
        request += byte
        if request.endswith(b"\r\n\r\n"):
            return request == REQUEST
    return False


def _relay(client, upstream, deadline):
    peers = {client: upstream, upstream: client}
    while peers:
        readable, _, _ = select(list(peers), [], [], max(0, deadline - monotonic()))
        if not readable:
            raise TimeoutError("Trial tunnel deadline reached")
        for source in readable:
            data = source.recv(4096)
            target = peers[source]
            if data:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Trial tunnel deadline reached")
                target.settimeout(remaining)
                target.sendall(data)
            else:
                del peers[source]
                target.shutdown(SHUT_WR)


def serve_one(listener, upstream_listener, *, timeout=3):
    """Relay a single fixed synthetic request. Caller owns both listeners."""
    proxy_address, destination = _endpoint(listener), _endpoint(upstream_listener)
    if proxy_address == destination:
        raise ValueError("Trial proxy and upstream must be distinct sockets")
    if not 0 < timeout <= 5:
        raise ValueError("Trial timeout must be within five seconds")
    deadline = monotonic() + timeout
    if not select([listener], [], [], timeout)[0]:
        raise TimeoutError("No client connected to the trial proxy")
    with listener.accept()[0] as client:
        if not _receive_request(client, deadline):
            client.settimeout(max(0, deadline - monotonic()))
            client.sendall(DENIED)
            return "DENIED"
        with socket(upstream_listener.family, SOCK_STREAM) as upstream:
            upstream.settimeout(max(0, deadline - monotonic()))
            upstream.connect(destination)
            client.settimeout(max(0, deadline - monotonic()))
            client.sendall(ACCEPTED)
            _relay(client, upstream, deadline)
            return "RELAYED"

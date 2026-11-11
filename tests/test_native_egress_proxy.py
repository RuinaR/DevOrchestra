from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
from select import select
import socket
import unittest

from devorchestra.lease import execution_slot
from devorchestra.native_egress_proxy import ACCEPTED, AUTHORITY, DENIED, REQUEST, serve_one


def listener(stack, family, host):
    server = stack.enter_context(socket.socket(family, socket.SOCK_STREAM))
    server.bind((host, 0))
    server.listen(1)
    server.settimeout(3)
    return server


def connect(stack, server):
    client = stack.enter_context(socket.socket(server.family, socket.SOCK_STREAM))
    client.settimeout(3)
    client.connect(server.getsockname())
    return client


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        packet = stream.recv(size - len(data))
        if not packet:
            break
        data += packet
    return bytes(data)


@unittest.skipUnless(os.name == "nt", "Windows execution slot")
class NativeEgressProxyTest(unittest.TestCase):
    def test_loopback_connect_relays_only_frozen_destination(self):
        with execution_slot():
            for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
                with self.subTest(host=host), ExitStack() as resources:
                    proxy = listener(resources, family, host)
                    approved = listener(resources, family, host)
                    unapproved = listener(resources, family, host)
                    executor = resources.enter_context(ThreadPoolExecutor(max_workers=1))

                    # Both upstream sockets must be reachable by the trusted positive control.
                    for upstream in (approved, unapproved):
                        with ExitStack() as control:
                            connect(control, upstream)
                            control.enter_context(upstream.accept()[0])

                    authority = f"[{host}]" if family == socket.AF_INET6 else host
                    target = f"{authority}:{unapproved.getsockname()[1]}".encode("ascii")
                    for denied_request in (REQUEST.replace(AUTHORITY, target),
                                           REQUEST.replace(b"Host: " + AUTHORITY, b"Host: " + target)):
                        future = executor.submit(serve_one, proxy, approved)
                        with ExitStack() as request:
                            client = connect(request, proxy)
                            client.sendall(denied_request)
                            self.assertEqual(DENIED, read_exact(client, len(DENIED)))
                        self.assertEqual("DENIED", future.result(timeout=5))
                        self.assertEqual([], select([approved, unapproved], [], [], 0)[0])

                    future = executor.submit(serve_one, proxy, approved)
                    with ExitStack() as tunnel:
                        client = connect(tunnel, proxy)
                        client.sendall(REQUEST + b"fixture")
                        self.assertEqual(ACCEPTED, read_exact(client, len(ACCEPTED)))
                        server = tunnel.enter_context(approved.accept()[0])
                        server.settimeout(3)
                        self.assertEqual(b"fixture", read_exact(server, 7))
                        server.sendall(b"verified")
                        self.assertEqual(b"verified", read_exact(client, 8))
                        client.shutdown(socket.SHUT_WR)
                        server.shutdown(socket.SHUT_WR)
                        self.assertEqual("RELAYED", future.result(timeout=5))
                    self.assertEqual([], select([unapproved], [], [], 0)[0])

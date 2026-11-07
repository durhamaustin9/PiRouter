#!/usr/bin/env python3
"""Relay one SSH connection to a fixed, administrator-configured TCP endpoint.

Install this file root-owned and invoke it from a restricted authorized_keys
forced command with literal --host/--port arguments. SSH_ORIGINAL_COMMAND is
deliberately never interpreted. This helper opens no listening port and grants
no shell or caller-selected forwarding destination.
"""
import argparse
import os
import selectors
import socket
import sys


BUFFER_LIMIT = 1024 * 1024
READ_SIZE = 64 * 1024


def prefer_bbr(connection):
    """Select BBR on supported Linux kernels; otherwise retain their default."""
    option = getattr(socket, 'TCP_CONGESTION', None)
    if option is None:
        return False
    try:
        connection.setsockopt(socket.IPPROTO_TCP, option, b'bbr')
    except OSError:
        return False
    return True


def relay(connection, input_fd=0, output_fd=1):
    """Copy both streams with bounded buffers and independent TCP half-closes."""
    input_open = True
    network_read_open = True
    network_write_open = True
    output_open = True
    to_network = bytearray()
    to_output = bytearray()
    input_blocking = os.get_blocking(input_fd)
    output_blocking = os.get_blocking(output_fd)
    connection.setblocking(False)
    os.set_blocking(input_fd, False)
    os.set_blocking(output_fd, False)
    try:
        with selectors.DefaultSelector() as selector:
            while True:
                # Rebuild three small interest sets. Full buffers stop reads
                # until their consumers make room, propagating backpressure.
                for key in list(selector.get_map().values()):
                    selector.unregister(key.fileobj)
                if not input_open and not to_network and network_write_open:
                    try:
                        connection.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    network_write_open = False
                if not network_read_open and not to_output and output_open:
                    # Convey the server's EOF even if it still accepts input.
                    # Waiting for stdin EOF here would deadlock half-closes.
                    os.close(output_fd)
                    output_open = False
                if not input_open and not network_read_open and not to_network and not to_output:
                    return
                if input_open and network_write_open and len(to_network) < BUFFER_LIMIT:
                    selector.register(input_fd, selectors.EVENT_READ, 'input')
                network_events = 0
                if network_read_open and len(to_output) < BUFFER_LIMIT:
                    network_events |= selectors.EVENT_READ
                if network_write_open and to_network:
                    network_events |= selectors.EVENT_WRITE
                if network_events:
                    selector.register(connection, network_events, 'network')
                if output_open and to_output:
                    selector.register(output_fd, selectors.EVENT_WRITE, 'output')
                if not selector.get_map():
                    raise OSError('Relay streams cannot make progress')
                for key, events in selector.select():
                    try:
                        if key.data == 'input':
                            data = os.read(input_fd, min(READ_SIZE, BUFFER_LIMIT - len(to_network)))
                            if data:
                                to_network.extend(data)
                            else:
                                input_open = False
                        elif key.data == 'output':
                            written = os.write(output_fd, to_output)
                            if written == 0:
                                raise BrokenPipeError('Relay output closed')
                            del to_output[:written]
                        else:
                            if events & selectors.EVENT_READ:
                                data = connection.recv(min(READ_SIZE, BUFFER_LIMIT - len(to_output)))
                                if data:
                                    to_output.extend(data)
                                else:
                                    network_read_open = False
                            if events & selectors.EVENT_WRITE and to_network:
                                written = connection.send(to_network)
                                if written == 0:
                                    raise BrokenPipeError('Relay destination closed')
                                del to_network[:written]
                    except (BlockingIOError, InterruptedError):
                        continue
    finally:
        os.set_blocking(input_fd, input_blocking)
        if output_open:
            os.set_blocking(output_fd, output_blocking)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=22)
    args = parser.parse_args(argv)
    if not args.host or not 1 <= args.port <= 65535:
        parser.error('A fixed host and a TCP port between 1 and 65535 are required')
    try:
        with socket.create_connection((args.host, args.port), timeout=15) as connection:
            prefer_bbr(connection)
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            relay(connection)
    except (OSError, ValueError) as error:
        print('Portable export relay: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

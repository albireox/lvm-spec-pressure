#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-04-30
# @Filename: server.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import html
import pathlib

from typing import TypeVar

import serial_asyncio
from sdsstools import get_logger, read_yaml_file


log = get_logger("lvm_spec_pressure")

T = TypeVar("T", bound="SpecPressureServer")


class SerialConnection:
    """Creates a connection to a serial device.

    Parameters
    ----------
    name
        The name of the device to which we are connecting.
        Mainly for logging purposes.
    url
        The path to the TTY device for the serial port.
    connection_params
        Other keyword parameters to be passed to
        ``serial_asyncio.open_serial_connection``.

    """

    def __init__(self, name: str, url: str, **connection_params):
        self.name = name
        self.url = url
        self.connection_params = connection_params

        self.rserial: asyncio.StreamReader | None = None
        self.wserial: asyncio.StreamWriter | None = None

    async def start_client(self):
        if self.wserial:
            log.info(f"{self.name}: Closing serial connection before restarting.")
            self.wserial.close()
            await self.wserial.wait_closed()

        self.rserial, self.wserial = await serial_asyncio.open_serial_connection(
            url=self.url, **self.connection_params.copy()
        )

        log.info(f"{self.name}: Serial {self.url} open.")

    async def readall(
        self,
        reader: asyncio.StreamReader,
        timeout=0.1,
        delimiter: str | None = None,
    ) -> bytes:
        """Reads the buffer until a delimiter or timeout."""

        if delimiter is not None and delimiter != "":
            try:
                return await asyncio.wait_for(
                    reader.readuntil(html.escape(delimiter).encode()),
                    timeout,
                )
            except asyncio.IncompleteReadError:
                log.error(f"{self.name}: IncompleteReadError while reading serial.")
                return b""
            except BaseException as err:
                log.error(f"{self.name}: Unknown error while reading serial: {err}")
                return b""

        else:
            reply = b""
            while True:
                try:
                    reply += await asyncio.wait_for(reader.readexactly(1), timeout)
                except asyncio.TimeoutError:
                    return reply
                except asyncio.IncompleteReadError:
                    log.error(f"{self.name}: IncompleteReadError while reading serial.")
                    return b""
                except BaseException as err:
                    log.error(f"{self.name}: Unknown error while reading serial: {err}")
                    return b""

    async def send_to_serial(
        self,
        data: bytes,
        timeout: float = 1.0,
        delimiter: str | None = None,
    ):
        """Sends data to the serial device and waits for a reply.."""

        await self.start_client()
        assert self.wserial and self.rserial

        self.wserial.write(data)
        await self.wserial.drain()
        log.info(f"{self.name}: sent {data}.")

        reply = b""
        try:
            reply = await self.readall(self.rserial, timeout, delimiter)
            log.info(f"{self.name}: received {reply}.")
        except asyncio.TimeoutError:
            log.error(f"{self.name}: timed out in readall()")
            self.wserial.close()
            await self.wserial.wait_closed()
        except BaseException as err:
            log.error(f"{self.name}: Unknown error in send_to_serial(): {err}")
            self.wserial.close()
            await self.wserial.wait_closed()
        finally:
            if self.wserial:
                self.wserial.close()

        return reply


class SpecPressureServer:
    """Creates a bidirectional bytestream between a TCP socket and serial ports.

    The connection is multiplexed to allow multiple connections. When a client sends
    a package to the serial port, the server will block other clients until the serial
    port has replied to the client or a timeout occurs.

    Parameters
    ----------
    spec
        The name of the spectrograph. Must match one of the top-level keys in the
        configuration file.
    camera
        The name of the spectrograph camera.
    port
        The port on which to start the TCP server. If not passed, uses the value
        defined in the configuration.
    timeout
        How long to wait for a reply from the serial server.
    delimiter
        String marking the end of a serial reply. If not provided, returns all the
        bytes received before timing out.
    debug
        If `True`, starts the file logger.

    """

    def __init__(
        self,
        spec: str,
        camera: str,
        port: int | None = None,
        timeout: float = 0.1,
        delimiter: str | None = None,
        debug: bool = False,
        config: dict | None = None,
    ):
        self.spec = spec
        self.camera = camera

        cwd = pathlib.Path(__file__).parent
        if config is None:
            config = read_yaml_file(cwd / "etc/lvm_spec_pressure.yaml")

        if (
            self.spec not in config["specs"]
            or self.camera not in config["specs"][self.spec]
        ):
            raise RuntimeError(f"Configuration not found for camera {self.camera}.")

        camera_config = config["specs"][self.spec][self.camera].copy()

        self.port = port or camera_config.get("port", None)
        self.timeout = timeout
        self.delimiter = delimiter or camera_config.get("delimiter", None)

        self._lock = asyncio.Lock()

        self.serial = SerialConnection(self.camera, **camera_config.get("device"))
        self.server: asyncio.AbstractServer | None = None

        if debug:
            log.start_file_logger("/home/lvm/logs/pressure/pressure.log")

    async def start(self: T) -> T:
        """Starts the TCP server."""

        self.server = await asyncio.start_server(
            self._client_connected_cb,
            "0.0.0.0",
            self.port,
        )

        return self

    async def stop(self):
        """Closes the TCP server."""

        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _client_connected_cb(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        """Handles a connected client."""

        log.info(f"{self.camera}: New connection.")
        while True:
            try:
                data = await reader.read(1024)
                if data == b"" or reader.at_eof():
                    log.info(f"{self.camera}: At EOF. Closing.")
                    writer.close()
                    await writer.wait_closed()
                    log.info(f"{self.camera}: At EOF. Did close.")
                    return

                log.info(f"{self.camera}: Received {data}.")

                async with self._lock:
                    reply = await self.serial.send_to_serial(
                        data,
                        delimiter=self.delimiter,
                        timeout=self.timeout,
                    )
                    if reply != b"":
                        log.info(f"{self.camera}: Sending {reply} to client.")
                        writer.write(reply)
                        await writer.drain()

            except ConnectionResetError:
                pass

            except BaseException as err:
                log.error(f"{self.camera}: Error found: {err}")

            finally:
                try:
                    writer.close()
                except BaseException:
                    log.error(f"{self.camera}: Failed closing writer during error.")
                    pass

                if self._lock.locked():
                    self._lock.release()

            return

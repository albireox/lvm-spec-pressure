#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-04-30
# @Filename: __main__.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import pathlib

import click
from sdsstools import read_yaml_file
from sdsstools.daemonizer import cli_coro

from lvm_spec_pressure.server import SpecPressureServer


@click.command()
@click.argument("SPEC", type=str)
@click.option("-c", "--config", type=str, help="Path to configuration file.")
@click.option("--debug", is_flag=True, help="Runs in debug mode.")
@cli_coro()
async def lvm_spec_pressure(spec: str, config: str | None = None, debug: bool = False):
    """Start a TCP-to-COM server."""

    if spec is None:
        raise click.MissingParameter("spec argument must be provided.")

    if config is not None:
        config_data = read_yaml_file(config)
    else:
        config = str(pathlib.Path(__file__).parent / "etc/lvm_spec_pressure.yaml")

    print("Config file", config)
    config_data = read_yaml_file(config)

    if spec not in config_data["specs"]:
        raise ValueError(f"spec {spec} not found in configuration.")

    cameras = list(config_data["specs"][spec].keys())

    servers = await asyncio.gather(
        *[SpecPressureServer(spec, camera).start() for camera in cameras]
    )

    await servers[0].server.serve_forever()


if __name__ == "__main__":
    lvm_spec_pressure()

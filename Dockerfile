FROM balenalib/raspberry-pi-python:3.11-buster

LABEL maintainer="gallegoj@uw.edu"

WORKDIR /opt

COPY . lvm_spec_pressure

RUN pip3 install -U pip setuptools wheel
RUN cd lvm_spec_pressure && pip3 install .

# Connect repo to package
LABEL org.opencontainers.image.source https://github.com/sdss/lvm_spec_pressure

ENTRYPOINT lvm-spec-pressure $SPEC

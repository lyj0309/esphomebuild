FROM ghcr.io/esphome/esphome:2026.8.2

RUN mv /usr/local/bin/esphome /usr/local/bin/esphome-local

COPY esphome-wrapper.py /usr/local/bin/esphome
RUN chmod 0755 /usr/local/bin/esphome

ENTRYPOINT ["esphome-device-builder"]

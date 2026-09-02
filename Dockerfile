FROM ghcr.io/esphome/esphome:2026.7.4

RUN mv /usr/local/bin/esphome /usr/local/bin/esphome-local \
    && pip install --no-cache-dir esphome-device-builder==1.11.5

COPY esphome-wrapper.py /usr/local/bin/esphome
RUN chmod 0755 /usr/local/bin/esphome

ENTRYPOINT ["esphome-device-builder"]

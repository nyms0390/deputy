# One container: virtual display, RDP session to Windows, the runtime and its HTTP API, and a TURN server that
# relays the live view's WebRTC stream to the browser.
# Company root certificates (certs/*.crt, passed in by compose); without them this stage is empty.
FROM scratch AS company-ca

FROM python:3.15-slim-trixie

# Trusts the company certificates for the build and at run time (the model APIs), and fetches Debian over HTTPS from
# DEBIAN_MIRROR: some networks refuse plain HTTP.
ARG DEBIAN_MIRROR=https://deb.debian.org
COPY --from=company-ca / /usr/local/share/ca-certificates/company/
RUN update-ca-certificates \
    && sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR%/}|g" /etc/apt/sources.list.d/debian.sources
# Python's TLS (httpx, uv, pip) reads the system store instead of its own bundle.
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt PIP_DISABLE_PIP_VERSION_CHECK=1
# uv from the package index (PYPI_INDEX when set), not ghcr.io, which some networks refuse.
ARG PYPI_INDEX=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "${PYPI_INDEX}" uv==0.13.5

# FreeRDP comes from backports: trixie's 3.15 doesn't tell Windows the clipboard changed when the new text has
# the same formats as the last, so Windows pastes the old text (fixed in 3.27).
RUN echo "deb ${DEBIAN_MIRROR%/}/debian trixie-backports main" > /etc/apt/sources.list.d/backports.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends xvfb xdotool xclip coturn \
    && apt-get install -y --no-install-recommends -t trixie-backports freerdp3-x11 \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8
# No download cache: the image installs once, and the cache was a quarter of its size.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
# PyYAML and zstandard have no wheels for Python 3.15 yet: a compiler builds them, and leaves with the same layer.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && uv sync --locked --no-dev --no-install-project \
    && apt-get purge -y --auto-remove gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY src ./src
RUN uv sync --locked --no-dev
COPY entrypoint.sh /usr/local/bin/desktop-entrypoint
COPY rdp-entrypoint.sh /usr/local/bin/rdp-entrypoint
ENV PATH="/app/.venv/bin:$PATH"

CMD ["rdp-entrypoint"]

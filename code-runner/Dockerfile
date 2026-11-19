# The code runner: where the coding agent's commands run (server.py). It has no network, so the libraries a task may
# need are installed here; compose.yaml runs it read-only, as a user without privileges, with its working folders in
# memory. `file` tells what an attached file is; unzip and poppler-utils open archives and PDFs.
# Company root certificates (certs/*.crt, passed in by compose); without them this stage is empty.
FROM scratch AS company-ca

FROM python:3.15-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Debian over HTTPS from DEBIAN_MIRROR (some networks refuse plain HTTP); pip from PYPI_INDEX when set.
ARG DEBIAN_MIRROR=https://deb.debian.org
ARG PYPI_INDEX
COPY --from=company-ca / /usr/local/share/ca-certificates/company/
RUN update-ca-certificates \
    && sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR%/}|g" /etc/apt/sources.list.d/debian.sources
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt PIP_INDEX_URL=${PYPI_INDEX:-https://pypi.org/simple}
RUN apt-get update && apt-get install -y --no-install-recommends file unzip poppler-utils && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir numpy==2.5.4 pandas==3.1.1 openpyxl==3.1.5 python-docx==1.2.0 python-pptx==1.0.2 pillow==13.0.0 pillow-heif==1.8.1 pypdfium2==5.15.2 \
    && useradd --uid 1000 --no-create-home --home-dir /workspace runner

COPY server.py sandbox.py /opt/code-runner/
USER runner
WORKDIR /workspace
EXPOSE 8080
CMD ["python", "-I", "/opt/code-runner/server.py"]

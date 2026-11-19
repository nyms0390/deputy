# The page, built once and served with /api passed on to the runtime.
# Company root certificates (certs/*.crt, passed in by compose); without them this stage is empty.
FROM scratch AS company-ca

FROM node:24-slim AS build
COPY --from=oven/bun:1.4.4 /usr/local/bin/bun /usr/local/bin/bun
# bun trusts the company certificates on top of its own, and installs from NPM_REGISTRY.
ARG NPM_REGISTRY=https://registry.npmjs.org/
ENV BUN_CONFIG_REGISTRY=${NPM_REGISTRY}
COPY --from=company-ca / /company-ca/
WORKDIR /web
COPY package.json bun.lock ./
RUN if ls /company-ca/*.crt >/dev/null 2>&1; then cat /company-ca/*.crt > /company-ca.pem \
        && export NODE_EXTRA_CA_CERTS=/company-ca.pem; fi \
    && bun install --frozen-lockfile
COPY . .
# Types are checked in CI: the tests they cover read fixtures from runtime/, outside this context.
RUN bun run vite build

FROM caddy:2.11.9-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv

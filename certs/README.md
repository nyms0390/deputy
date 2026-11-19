# Company root certificates

On a network whose proxy inspects HTTPS, put the company's root certificate here as a PEM `.crt` file (text that
starts with `-----BEGIN CERTIFICATE-----`). `docker compose build` then trusts it in every image: apt, pip, uv and bun
while building, and the runtime's calls to the model APIs while running. Leave the folder without certificates
elsewhere.

`.crt` files here stay out of git.

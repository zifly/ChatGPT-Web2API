FROM python:3.11-slim-trixie

RUN apt-get update && apt-get install -y \
    ca-certificates curl gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub -o /tmp/google.asc \
    && gpg --batch --dearmor -o /etc/apt/keyrings/google.gpg /tmp/google.asc \
    && rm /tmp/google.asc \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google.list \
    && apt-get update && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# Persistent Chrome profile (stores login session)
VOLUME /data/chrome-profile

# Cookie file mount point (for headed-mode auth on display-less servers)
VOLUME /data/cookies

# Headless is OFF by default. Headless Chrome triggers ChatGPT's bot
# detection (see README "Limitations"), so the default is headed mode.
# On a display-less server, run with VNC/Xvfb (see docs/deployment.md) or
# set W2A_HEADLESS=true only if you accept the anti-bot risk and have a
# cookie-injection fallback. See docker-entrypoint.sh.
ENV W2A_HEADLESS=false
ENV W2A_USER_DATA_DIR=/data/chrome-profile
ENV W2A_PORT=8080
ENV W2A_HOST=0.0.0.0
ENV W2A_CHROME_PATH=/usr/local/bin/docker-chrome

EXPOSE 8080 9222

# Start script handles cookie injection
COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY docker-chrome.sh /usr/local/bin/docker-chrome
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-chrome && chmod +x /usr/local/bin/docker-chrome

ENTRYPOINT ["/docker-entrypoint.sh"]

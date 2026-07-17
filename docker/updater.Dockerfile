# Self-update sidecar: docker CLI + compose plugin (bundled in docker:cli) + git + bash.
# It bind-mounts the host repo and docker socket to run scripts/update.sh on the host daemon.
FROM docker:27-cli

# curl: so update.sh can DM the owner the update's outcome (success/failure) via the Bot API.
RUN apk add --no-cache bash git curl

COPY scripts/updater.sh /usr/local/bin/updater.sh
RUN chmod +x /usr/local/bin/updater.sh

ENTRYPOINT ["/usr/local/bin/updater.sh"]

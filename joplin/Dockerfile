FROM node:20-alpine

# Install Joplin CLI and socat (used to forward 0.0.0.0:41184 → 127.0.0.1:41185)
RUN apk add --no-cache socat && npm install -g joplin

# Data directory (mounted as volume so notes persist across restarts)
RUN mkdir -p /root/.config/joplin

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 41184

ENTRYPOINT ["/entrypoint.sh"]

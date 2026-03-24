FROM node:20-alpine

# Install Joplin CLI
RUN npm install -g @joplin/cli

# Data directory (mounted as volume so notes persist across restarts)
RUN mkdir -p /root/.config/joplin

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 41184

ENTRYPOINT ["/entrypoint.sh"]

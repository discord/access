# Agent image for the AI review gate.
#
# Holds the pinned agent CLIs and nothing else; the review runner supplies every input through
# read-only mounts and stdin, and the container never receives a credential. The proxy container
# reuses this image for its Node runtime.
FROM node:24.22.2-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9

# ripgrep backs Claude Code's Grep tool; USE_BUILTIN_RIPGREP=0 in the agent environment selects it.
RUN apt-get update \
  && apt-get install --yes --no-install-recommends ca-certificates ripgrep \
  && rm --recursive --force /var/lib/apt/lists/*

WORKDIR /opt/agent
COPY package.json package-lock.json ./
# --ignore-scripts: the CLIs' install scripts only copy the platform binary into place, so link
# the binaries directly instead of running third-party code at build time.
RUN npm ci --ignore-scripts --omit=dev --no-audit --no-fund \
  && case "$(dpkg --print-architecture)" in amd64) platform=x64 ;; arm64) platform=arm64 ;; *) exit 1 ;; esac \
  && ln -s "/opt/agent/node_modules/@anthropic-ai/claude-code-linux-${platform}/claude" /usr/local/bin/claude \
  && ln -s /opt/agent/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
  && claude --version \
  && codex --version

# The image's built-in `node` user (uid 1000); the runner also passes --user 1000:1000.
USER node

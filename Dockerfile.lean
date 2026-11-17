FROM research-cluster:local
RUN apt-get update && apt-get install -y --no-install-recommends curl zstd \
    && curl --fail --location --retry 3 https://github.com/leanprover/lean4/releases/download/v4.35.0/lean-4.35.0-linux.tar.zst -o /tmp/lean.tar.zst \
    && echo '7289597a44543246c9227b8cae501767e095ce17f6d448b95fc4a8dc97268f6d  /tmp/lean.tar.zst' | sha256sum -c - \
    && mkdir -p /opt/lean \
    && tar --zstd -xf /tmp/lean.tar.zst --strip-components=1 -C /opt/lean \
    && rm /tmp/lean.tar.zst \
    && rm -rf /var/lib/apt/lists/*
ENV CLUSTER_LEAN_HOME=/opt/lean
ENV PATH="/opt/lean/bin:${PATH}"
CMD ["worker", "--pool", "lean-cpu", "--cpus", "1", "--memory-mb", "1024", "--concurrency", "1"]

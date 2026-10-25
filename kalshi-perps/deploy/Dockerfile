# Existing host image supplies Python 3.12, pinned uv, tini and uid 10001.
FROM amat-executor:vps-20261008
USER root
ENV UV_COMPILE_BYTECODE=0 UV_CONCURRENT_INSTALLS=1 UV_CONCURRENT_DOWNLOADS=1 \
    PYTHONPATH=/opt/kalshi-perps/src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
    POLARS_MAX_THREADS=1 MPLCONFIGDIR=/tmp/matplotlib
WORKDIR /opt/kalshi-perps
COPY kalshi-perps/deploy/requirements.txt /tmp/perps-requirements.txt
RUN uv venv /opt/perps-venv && uv pip sync --python /opt/perps-venv/bin/python --require-hashes /tmp/perps-requirements.txt
COPY kalshi-perps/src ./src
COPY kalshi-perps/configs ./configs
COPY kalshi-perps/deploy ./deploy
COPY kalshi-perps/deploy/paper.toml ./configs/paper.toml
LABEL amat.mode="paper" amat.engine="kalshi-perps"
COPY amat/src /opt/research/src
COPY release-manifest.json ./release-manifest.json
RUN mkdir -p /var/lib/kalshi-perps && chown executor:executor /var/lib/kalshi-perps
ENV PATH=/opt/perps-venv/bin:$PATH
USER executor
CMD ["python", "deploy/supervise.py"]

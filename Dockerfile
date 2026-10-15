FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg

WORKDIR /app

# Install dependencies first so Docker caches this layer until pyproject.toml changes.
# rasterio ships GDAL inside its manylinux wheel: no system packages needed.
COPY pyproject.toml ./
COPY src ./src
RUN pip install -e ".[dev]"

COPY . .

CMD ["s2veg", "run", "--config", "configs/madrid.toml", "--out", "outputs"]

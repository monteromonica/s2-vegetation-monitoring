FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg

WORKDIR /app

# rasterio bundles GDAL in its wheel, but the slim base image lacks libexpat (XML parsing).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first so Docker caches this layer until pyproject.toml changes.
COPY pyproject.toml ./
COPY src ./src
RUN pip install -e ".[dev]"

COPY . .

CMD ["s2veg", "run", "--config", "configs/madrid.toml", "--out", "outputs"]

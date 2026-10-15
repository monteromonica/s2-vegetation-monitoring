# s2-vegetation-monitoring

A small, reproducible **Sentinel-2 L2A** vegetation-monitoring pipeline, built as a hands-on way to
practise the core skills of an Earth Observation processing engineer:

- finding data with **STAC** and reading it from **S3** / HTTPS without downloading whole scenes,
- querying **Sentinel Hub Process API**, **openEO** and **CDSE object storage** with the right credentials,
- handling **reflectance scaling, cloud masking and raster grids** correctly,
- building **temporal composites**, spectral indices and an exploratory clustering,
- **validating** against an independent reference (ESA WorldCover),
- packaging everything in **Docker** with tests and run provenance.

It is a personal learning project, not an operational or scientifically validated product.
See [Limitations](#limitations).

## Quick start

```bash
docker compose build
docker compose run --rm s2veg pytest -q
docker compose run --rm s2veg s2veg search --config configs/madrid.toml
docker compose run --rm s2veg s2veg access-demo --config configs/madrid.toml
docker compose run --rm s2veg s2veg run --config configs/madrid.toml --out outputs
```

To match WorldCover 2021, use `configs/madrid_2021.toml` (March–September 2021) instead.

CDSE commands need a `.env` (see below). They are optional; `search` / `run` / `access-demo` do not.

## How to reach Sentinel-2 data

Four paths, same AOI (Madrid, MGRS tile 30TVK). Do not mix credentials.

| | STAC + COG | Process API | openEO | CDSE SAFE/JP2 |
|---|---|---|---|---|
| Command | `s2veg run` | `s2veg hub-fetch` | `s2veg openeo-ndvi` | `s2veg cdse-s3` |
| Who processes | You | Server (evalscript) | Server (process graph) | You |
| Auth | none | OAuth **client** (`sh-…`) | same OAuth client | CDSE **S3** access+secret |
| What you get | Raw bands, windowed reads | Recorte FLOAT32 10 m UTM | Median SCL-masked NDVI | Original JP2 window |
| Watch out for | Provider scale/offset flags | `sampleType` AUTO → 8-bit; unpinned grid | Half-open time interval; credits | Path-style S3; slower than COG |

A fifth, public archive appears in `s2veg access-demo`: the Element84/AWS JP2 bucket
(`sentinel-s2-l2a`). That one was readable unsigned when last checked. It is **not** CDSE `eodata`.

The default `s2veg run` uses **Earth Search COGs**. CDSE STAC lists the same scenes as SAFE JP2s.

## Commands

| Command | Needs `.env` | What it does |
|---|---|---|
| `search` | no | Earth Search STAC, scenes that cover the AOI |
| `cdse-search` | no | Same AOI in the CDSE STAC catalogue (public) |
| `access-demo` | no | HTTPS vs S3 COG, AWS JP2 HEAD, offset check |
| `run` | no | Full pipeline: composites, clustering, WorldCover |
| `hub-token` | OAuth | Authenticate only; never prints the token |
| `hub-fetch` | OAuth | Process API cut-out (B02, B03, B04, B08, SCL) |
| `openeo-ndvi` | OAuth | CDSE openEO median NDVI |
| `cdse-s3` | S3 keys | One JP2 window from `eodata` |

## Copernicus Data Space Ecosystem

[Documentation](https://documentation.dataspace.copernicus.eu/APIs.html). Each API has its own secrets.

| API | Role in this project | Auth |
|---|---|---|
| **STAC catalogue** `stac.dataspace.copernicus.eu` | `s2veg cdse-search` | none |
| **Sentinel Hub Process** `sh.dataspace.copernicus.eu` | `s2veg hub-fetch` | OAuth **client** (`sh-…`) |
| **openEO** `openeo.dataspace.copernicus.eu` | `s2veg openeo-ndvi` | same OAuth client (no browser) |
| **S3** `eodata.dataspace.copernicus.eu` | `s2veg cdse-s3` | CDSE S3 access+secret (**not** the OAuth client) |
| OData | not wired | OIDC |

### OAuth client (not User ID / Account ID)

On [shapps.dataspace.copernicus.eu/dashboard/#/account/settings](https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings)
you will see User ID and Account ID. Those identify *you*. Process API and openEO need a **machine client**:

1. Same page, **OAuth clients** → **Create**.
2. Grant type **Client Credentials**, not SPA.
3. Copy **Client ID** (`sh-…`) and **Client secret** (shown once).

```
SH_PROVIDER=cdse
SH_CLIENT_ID=sh-...
SH_CLIENT_SECRET=...
```

```bash
docker compose run --rm s2veg s2veg hub-token
docker compose run --rm s2veg s2veg hub-fetch --config configs/madrid.toml --start 2024-03-01 --end 2024-03-31 --out outputs/hub_madrid_202403.tif
docker compose run --rm s2veg s2veg openeo-ndvi --config configs/madrid.toml --start 2024-03-01 --end 2024-03-31 --out outputs/openeo_madrid_202403_ndvi.tif
```

Process API returns the four 10 m bands + SCL (you compute NDVI locally). openEO returns
SCL-masked median NDVI. Same AOI, same OAuth client, different contract: evalscript vs process graph.

openEO intervals are half-open `[start, end)`: the command adds one day so `--end` is included.
Rasterio 1.4+ will not take `AWS_ACCESS_KEY_ID` inside `rasterio.Env()`; `cdse-s3` uses an `AWSSession`.

A Planet API key (`PLA…`) and the Planet Insights OAuth client are a different provider
(`SH_PROVIDER=sentinelhub`). They do not open CDSE.

### CDSE S3 keys (not the OAuth client)

Create keys at
[eodata-s3keysmanager.dataspace.copernicus.eu](https://eodata-s3keysmanager.dataspace.copernicus.eu/)
(secret shown once):

```
CDSE_S3_ACCESS_KEY=...
CDSE_S3_SECRET_KEY=...
```

```bash
docker compose run --rm s2veg s2veg cdse-s3 --config configs/madrid.toml
```

GDAL talks to `eodata.dataspace.copernicus.eu` with **path-style** requests (`AWS_VIRTUAL_HOSTING=FALSE`).

## Pipeline

```
STAC search ──► scenes that fully cover the AOI (one MGRS tile)
      │
      ├─► SCL (20 m) onto the 10 m grid ──► AOI valid fraction ──► best N scenes per month
      │
      ├─► B02,B03,B04,B08 ──► DN × scale (+ offset if not already applied) ──► physical sanity check
      │        ──► mask with SCL ──► NDVI, NDWI (McFeeters), EVI
      │
      ├─► monthly median composite (+ observation count per pixel)
      ├─► K-means on standardised features (exploratory)
      └─► comparison with ESA WorldCover: vegetation F1, NDVI per class, seasonality
```

Outputs in `outputs/<name>/`: georeferenced GeoTIFFs, figures drawn in map coordinates,
`scenes.csv` (every scene considered and why it was kept or skipped), `validation.json`,
`clusters.json`, `run_metadata.json` (config, grid, scene IDs, scale/offset used, versions).

## Design decisions worth being able to explain

- **One explicit grid.** Derived from product metadata, snapped to the 20 m SCL grid so the cloud
  mask upsamples as an exact 2x2 replication. Categorical rasters use nearest-neighbour only.
- **Cloud filter at AOI level.** `eo:cloud_cover` describes a 100x100 km tile; what matters is the valid
  fraction inside the AOI, computed from SCL.
- **Metadata is checked against physics.** Earth Search COGs advertise an offset of -0.1 *and* flag it as
  already applied. Applying both gives negative reflectance over ~90% of water pixels, so the pipeline
  honours the flag and a guard rejects implausible data.
- **Bands by name, never by position.**
- **Validation without leakage.** Nothing is trained on WorldCover; the NDVI threshold is fixed a priori.

## Limitations

- Exploratory: no ground truth collected by us. WorldCover is itself a model product.
  `madrid_2021.toml` matches the map year; F1 still drops in summer because grassland/crops dry
  while WorldCover stays a static annual map. Pixel-level agreement overstates certainty:
  neighbouring pixels are correlated.
- Clusters are not land-cover classes; their meaning is read from centroids.
- NDVI saturates over dense canopy and is sensitive to soil background and SCL quality. EVI is dropped
  where numerically unstable and the dropped fraction is reported.
- Single AOI inside one MGRS tile and one WorldCover tile.
- The COG offset behaviour was measured on Earth Search for baselines 05.xx; re-check it if you
  switch provider. Original JP2s (CDSE `eodata`) still need the ESA −0.1 offset from baseline 04.00.
- `hub-fetch` / `openeo-ndvi` consume CDSE credits. The OAuth client is a *service account*;
  its balance is not automatically the same as your personal CDSE login.
- Process API, openEO and the local pipeline do not share a bit-identical grid unless CRS, origin
  and resolution are pinned the same way.
- No uncertainty propagation, no terrain/BRDF correction, no sensor harmonisation beyond the provider's.

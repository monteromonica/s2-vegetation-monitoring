# s2-vegetation-monitoring

A small, reproducible **Sentinel-2 L2A** vegetation-monitoring pipeline, built as a hands-on way to
practise the core skills of an Earth Observation processing engineer:

- finding data with **STAC** and reading it from **S3** / HTTPS without downloading whole scenes,
- querying the **Sentinel Hub Process API** and understanding what it does for you,
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

## Three ways to reach Sentinel-2 data

| | Sentinel Hub Process API | STAC + COG (HTTPS or S3) | Original JP2 archive (S3) |
|---|---|---|---|
| Who processes | The server: crop, mosaic, resample, evalscript | You | You |
| Credentials | OAuth client (account required) | None | None (checked: anonymous read works) |
| What you get | Pixels for your AOI, on a grid the server picks unless you pin it | Raw bands, byte-range reads | Raw JP2 bands |
| Strength | Fast answer, no raster code | Fully explicit and reproducible, efficient windowed reads | Original ESA files |
| Watch out for | Output `sampleType` (default scales to 8-bit), grid, processing units, server-side versioning | Provider-specific scaling/offset metadata | Paths differ from catalogue hrefs; JP2 windowed reads are slower |

`s2veg access-demo` measures this instead of asserting it: same pixels over HTTPS and S3,
bucket access without credentials, COG vs JP2 timing, and the radiometric-offset check.

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

- Exploratory: no ground truth collected by us. WorldCover is itself a model product (2021 vs 2024
  imagery), and pixel-level agreement overstates certainty because neighbouring pixels are correlated.
- Clusters are not land-cover classes; their meaning is read from centroids.
- NDVI saturates over dense canopy and is sensitive to soil background and SCL quality. EVI is dropped
  where numerically unstable and the dropped fraction is reported.
- Single AOI inside one MGRS tile and one WorldCover tile.
- The offset behaviour above was measured on Earth Search COGs for baselines 05.10/05.11; re-check it if
  you switch provider.
- `hub-fetch` follows the public API documentation and has not been run against a real account yet.
- No uncertainty propagation, no terrain/BRDF correction, no sensor harmonisation beyond the provider's.

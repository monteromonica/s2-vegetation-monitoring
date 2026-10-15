# s2-vegetation-monitoring

Reproducible vegetation monitoring with **Sentinel-2 L2A**: STAC discovery, AOI-level cloud
masking with the SCL layer, correct reflectance scaling, spectral indices, monthly median
composites, exploratory clustering, and a sanity check against **ESA WorldCover**.

This is a personal learning project and a rewrite of an earlier exploration
([`monteromonica/vegetation`](https://github.com/monteromonica/vegetation)). It is **not** an
operational or scientifically validated product. See [Limitations](#limitations).

## What changed from v1 and why

| v1 problem | Consequence | v2 fix |
|---|---|---|
| Hardcoded tile `30/T/YN` (Madrid is `30TVK`) | The S3 route never covered Madrid | Scenes come from a STAC search over the AOI; the grid comes from product metadata |
| Bands read by array position; S3 wrote B,G,R,NIR, Hub wrote R,G,B,NIR | NDVI/EVI computed on the wrong bands for one source | Bands are addressed by **name**; test shuffles the order |
| Raw DN fed into EVI (`+1` assumes reflectance 0..1); Hub default `sampleType` (`AUTO`) is, per its docs, 8-bit 0..255 (hypothesis, consistent with the numbers) | EVI max of 85 and 25,925 in the committed CSVs | `scale`/`offset` read from metadata; a guard rejects data that is not reflectance; Hub request pins `FLOAT32` |
| No cloud/shadow mask (`maxCloudCoverage` is per **scene**, 100x100 km) | Clouds entered the NDVI | SCL mask per pixel; scenes filtered by **valid fraction of the AOI** |
| 512x512 pixels over an arbitrary bbox | Non-square, resampled pixels; `resolution=10` unused | One explicit 10 m grid, snapped to the 20 m SCL grid |
| K-means on NDVI alone, called a "classification" | Histogram slicing, not land cover | Multi-feature, standardised, seeded; labelled exploratory; compared to a reference |
| Tests built a 3-band array for code reading band index 3 | Tests could not pass | Tests use named bands and synthetic GeoTIFFs |
| README said the bucket is public; code used `--request-payer` | Contradictory, needs AWS credentials | `s2veg access-demo` shows what is and is not anonymous |

## Quick start (Docker)

```bash
docker compose build
docker compose run --rm s2veg pytest -q          # unit tests
docker compose run --rm s2veg s2veg search --config configs/madrid.toml
docker compose run --rm s2veg s2veg access-demo --config configs/madrid.toml
docker compose run --rm s2veg                    # full pipeline -> ./outputs/madrid
```

## Pipeline

```
STAC search ──► scenes covering the AOI (one MGRS tile)
      │
      ├─► read SCL (20 m) onto the 10 m grid ──► AOI valid fraction ──► keep best N scenes / month
      │
      ├─► read B02,B03,B04,B08 ──► DN × scale + offset ──► mask with SCL ──► NDVI, NDWI, EVI
      │
      ├─► monthly median composite (+ observation count per pixel)
      ├─► K-means (exploratory)
      └─► compare with ESA WorldCover (binary vegetation, per-class NDVI, seasonality)
```

Outputs in `outputs/<name>/`: georeferenced GeoTIFFs, figures with map coordinates,
`scenes.csv` (every scene considered and why it was kept/skipped), `validation.json`,
`clusters.json`, and `run_metadata.json` (config, grid, scene IDs, scale/offset, versions).

## Three ways to reach Sentinel-2 data

| | Sentinel Hub Process API | STAC + COG over HTTPS/S3 (default) | Legacy JP2 archive on S3 |
|---|---|---|---|
| Who processes | Server (crop, mosaic, resample) | You | You |
| Credentials | Yes (OAuth) | None | AWS account |
| Cost | Processing units | Free | Requester pays transfer |
| Reproducibility | Depends on server evalscript/version | Fully explicit | Explicit, but heavy JP2 |

## Limitations

- Exploratory: no ground truth collected by us. WorldCover is itself a model product (2021 vs 2024
  imagery) and pixel-level agreement overstates certainty because neighbouring pixels are correlated.
- Clusters are not land-cover classes. Their meaning is read from centroids.
- NDVI saturates over dense canopy and is sensitive to soil background and to the SCL mask quality;
  EVI is dropped where it is numerically unstable and the dropped fraction is reported.
- Single AOI in one tile; AOIs spanning two MGRS tiles or two WorldCover tiles are not supported.
- `hub-fetch` (Sentinel Hub) was written from the public API documentation and has not been run
  against a real account.
- No uncertainty propagation, no BRDF/terrain correction, no sensor harmonisation beyond what the
  provider applies.

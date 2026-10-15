import pytest

from s2veg import cdse_s3


def test_parse_s3_uri_accepts_the_forms_cdse_uses():
    href = "s3://eodata/Sentinel-2/MSI/L2A/2024/03/15/X.SAFE/GRANULE/Y/IMG_DATA/R10m/B04.jp2"
    assert cdse_s3.parse_s3_uri(href) == (
        "eodata",
        "Sentinel-2/MSI/L2A/2024/03/15/X.SAFE/GRANULE/Y/IMG_DATA/R10m/B04.jp2",
    )
    assert cdse_s3.parse_s3_uri("/eodata/Sentinel-2/MSI/L2A/x.jp2") == ("eodata", "Sentinel-2/MSI/L2A/x.jp2")
    with pytest.raises(ValueError, match="eodata"):
        cdse_s3.parse_s3_uri("https://example.com/file.jp2")


def test_gdal_options_are_path_style_on_the_cdse_host():
    env = cdse_s3.gdal_options()
    assert env["AWS_S3_ENDPOINT"] == "eodata.dataspace.copernicus.eu"
    assert env["AWS_VIRTUAL_HOSTING"] == "FALSE"
    assert env["AWS_HTTPS"] == "YES"
    assert "https://" not in env["AWS_S3_ENDPOINT"]
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_missing_s3_keys_do_not_mention_oauth(monkeypatch):
    monkeypatch.delenv("CDSE_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("CDSE_S3_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CDSE_S3_ACCESS_KEY"):
        cdse_s3.credentials()


def test_rasterio_session_targets_cdse_endpoint():
    opts = cdse_s3.rasterio_session("AK", "secret").get_credential_options()
    assert opts["AWS_ACCESS_KEY_ID"] == "AK"
    assert "dataspace.copernicus.eu" in opts["AWS_S3_ENDPOINT"]


def test_jp2_offset_follows_esa_baseline_04():
    assert cdse_s3.jp2_offset("03.01") == 0.0
    assert cdse_s3.jp2_offset("04.00") == -0.1
    assert cdse_s3.jp2_offset("05.11") == -0.1
    assert cdse_s3.jp2_offset(None) == -0.1

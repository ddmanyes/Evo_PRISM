"""
Tests for CRC Visium HD L3 data accessibility and L2 Parquet conversion.
Skipped automatically if test data is not available.
"""


def test_crc_official_structure(l3_crc_path):
    """Verify CRC official_v4 has expected directory structure."""
    assert (l3_crc_path / "binned_outputs").exists()
    assert (l3_crc_path / "spatial").exists()


def test_crc_8um_exists(l3_crc_path):
    """Verify 8µm binned output (primary L2 target) exists."""
    path_8um = l3_crc_path / "binned_outputs" / "square_008um"
    assert path_8um.exists(), f"8µm binned output not found at {path_8um}"


def test_crc_segmented_outputs(l3_crc_path):
    """Verify segmented outputs exist (cell segmentation results)."""
    seg_path = l3_crc_path / "segmented_outputs"
    assert seg_path.exists(), "segmented_outputs missing"

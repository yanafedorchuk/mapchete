"""
Raster file input which can be read by rasterio.

Currently limited by extensions .tif, .vrt., .png and .jp2 but could be
extended easily.
"""

import os
import rasterio
import ogr
from shapely.geometry import box
from shapely.wkt import loads
from cached_property import cached_property
from copy import deepcopy

from mapchete.formats import base
from mapchete.io.vector import reproject_geometry
from mapchete.io.raster import read_raster_window


class InputData(base.InputData):
    """Main input class."""

    METADATA = {
        "driver_name": "raster_file",
        "data_type": "raster",
        "mode": "r",
        "file_extensions": ["tif", "vrt", "png", "jp2"]
    }

    def __init__(self, input_params):
        """Initialize."""
        super(InputData, self).__init__(input_params)
        self.path = input_params["path"]

    @cached_property
    def profile(self):
        """Read raster metadata."""
        with rasterio.open(self.path, "r") as src:
            return deepcopy(src.meta)

    def open(self, tile, **kwargs):
        """Return InputTile."""
        return InputTile(tile, self, **kwargs)

    def bbox(self, out_crs=None):
        """Return data bounding box."""
        assert self.path
        assert self.pyramid
        if out_crs is None:
            out_crs = self.pyramid.crs
        with rasterio.open(self.path) as inp:
            inp_crs = inp.crs
            try:
                assert inp_crs.is_valid
            except AssertionError:
                raise IOError("CRS could not be read from %s" % self.path)
        out_bbox = bbox = box(
            inp.bounds.left, inp.bounds.bottom, inp.bounds.right,
            inp.bounds.top)
        # If soucre and target CRSes differ, segmentize and reproject
        if inp_crs != out_crs:
            segmentize = _get_segmentize_value(self.path, self.pyramid)
            try:
                ogr_bbox = ogr.CreateGeometryFromWkb(bbox.wkb)
                ogr_bbox.Segmentize(segmentize)
                segmentized_bbox = loads(ogr_bbox.ExportToWkt())
                bbox = segmentized_bbox
            except:
                raise
            try:
                return reproject_geometry(
                    bbox, src_crs=inp_crs, dst_crs=out_crs)
            except:
                raise
        else:
            return out_bbox

    def exists(self):
        """Check whether input file exists."""
        return os.path.isfile(self.path)


class InputTile(base.InputTile):
    """Target Tile representation of input data."""

    def __init__(self, tile, raster_file, resampling="nearest"):
        """Initialize."""
        self.tile = tile
        self.raster_file = raster_file
        self._np_band_cache = {}
        self.resampling = resampling

    def read(self, indexes=None):
        """Read reprojected and resampled numpy array for current Tile."""
        band_indexes = self._get_band_indexes(indexes)
        if len(band_indexes) == 1:
            return self._bands_from_cache(indexes=band_indexes).next()
        else:
            return self._bands_from_cache(indexes=band_indexes)

    def is_empty(self, indexes=None):
        """Check if there is data within this tile."""
        band_indexes = self._get_band_indexes(indexes)
        src_bbox = self.raster_file.bbox()
        tile_geom = self.tile.bbox

        # empty if tile does not intersect with file bounding box
        if not tile_geom.intersects(src_bbox):
            return True

        # empty if source band(s) are empty
        all_bands_empty = True
        for band in self._bands_from_cache(band_indexes):
            if not band.mask.all():
                all_bands_empty = False
                break
        return all_bands_empty

    def _get_band_indexes(self, indexes=None):
        """Return valid band indexes."""
        if indexes:
            if isinstance(indexes, list):
                return indexes
            else:
                return [indexes]
        else:
            return range(1, self.raster_file.profile["count"]+1)

    def _bands_from_cache(self, indexes=None):
        """Cache reprojected source data for multiple usage."""
        band_indexes = self._get_band_indexes(indexes)
        for band_index in band_indexes:
            if band_index not in self._np_band_cache:
                band = read_raster_window(
                    self.raster_file.path,
                    self.tile,
                    indexes=band_index,
                    resampling=self.resampling
                ).next()
                self._np_band_cache[band_index] = band
            yield self._np_band_cache[band_index]


def _get_segmentize_value(input_file, tile_pyramid):
    """Return the recommended segmentation value in input file units."""
    with rasterio.open(input_file, "r") as input_raster:
        pixelsize = input_raster.transform[0]
    return pixelsize * tile_pyramid.tile_size
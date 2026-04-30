# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed
- Unused `py7zr` dependency.

### Changed
- Loosened `pandas`, `requests`, and `pydantic` lower bounds to permit older compatible versions.

## [0.1.0] — initial public release

First public release. Milan-only OMI fair-price estimator with FastAPI + Jinja
front end, OSM amenity counting via Overpass, and Nominatim geocoding.

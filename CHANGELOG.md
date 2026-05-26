# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


First public release. Milan-only OMI fair-price estimator with FastAPI + Jinja
front end, OSM amenity counting via Overpass, and Nominatim geocoding.

## v0.11.0 (2026-05-26)

### Feat

- add MCP server for Milan valuation pipeline

## v0.9.0 (2026-05-23)

### Feat

- derive OMI condition from fine condition with alternatives fallback

## v0.8.1 (2026-05-16)

### Fix

- **data**: pick closest OMI condition when exact match unavailable

## v0.8.0 (2026-05-14)

### Feat

- **web**: improve amenity fetch failure UX with inline retry and warnings
- **web**: add /api/amenities endpoint with structured error responses
- **amenities**: add Overpass retry with exponential backoff

## v0.7.2 (2026-05-12)

### Fix

- **docker**: compile Babel catalogs in builder stage to ship translations

## v0.7.1 (2026-05-12)

### Fix

- **web**: fix segmented control overflow by rebalancing form columns

## v0.7.0 (2026-05-12)

### Feat

- **web**: render lift breakdown and update coefficient enumeration
- **valuation**: recalibrate coefficients with research citations

## v0.6.0 (2026-05-10)

### Feat

- **web**: favicon fallbacks, OG metadata, and theme color
- **web**: vendor Leaflet 1.9.4 from unpkg
- **web**: self-host fonts (Latin + Latin-Ext subset, woff2)
- **web**: content-hashed URLs and immutable cache for /static assets

### Fix

- **web**: tighten template quoting and trim dead code in app bootstrap

## v0.5.0 (2026-05-09)

### Feat

- make bookmarklet demo click-driven with live cursor calibration

## v0.4.2 (2026-05-08)

### Fix

- prevent redirect loop on lang-prefixed unknown paths
- redirect bare paths to localized routes

## v0.4.0 (2026-05-07)

### Feat

- add exposure and room-density adjustments to property valuation

### Fix

- **ci**: simplify translations job by treating .mo as build artifacts
- **ci**: scope translation staleness check to .pot/.po, exclude .mo binaries

## v0.2.2 (2026-05-06)

## v0.2.1 (2026-05-06)

### Fix

- resolve ruff linting violations and pybabel format flag issues

## v0.2.0 (2026-05-05)

### Feat

- add i18n support with Italian and English translations

## v0.1.1 (2026-05-04)

### Fix

- derive app_version from package metadata in templates

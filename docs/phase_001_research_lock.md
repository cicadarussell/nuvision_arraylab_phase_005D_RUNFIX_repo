# Phase 001 Research Lock

## Chosen stack

| Layer | Selected |
|---|---|
| API | FastAPI |
| Spatial database | PostgreSQL + PostGIS |
| Product/spec validation | Pydantic |
| ORM/migrations | SQLAlchemy + Alembic |
| Solar maths later | pvlib + PVGIS backend calls |
| Geometry later | Shapely + PostGIS |
| Optimisation later | OR-Tools |
| Frontend map | MapLibre GL JS |
| Frontend 3D | Three.js |
| Spreadsheet IO | controlled XLSX imports with staging/diff/approval |

## Why this is the right first phase

The temptation is to build the shiny 3D roof tool first. That is how humans make beautiful wreckage.

The correct foundation is traceable product data, immutable calculation packets, versioned assumptions, staged spreadsheet imports, and explicit structural responsibility boundaries.

# PSNI Crime Map Project 👮‍♂️

![image](https://github.com/user-attachments/assets/d4cdf87a-4cb3-4096-b0ec-b591604e8c09)


## Overview 📖
This Project was started in the summer of 2023 and was my first ML project. I started this project in anticipation of my ML dissertation project at QUB. 

This project focuses on the analysis of crime data using machine learning techniques to identify high-density crime areas and visualise them on maps. The goal is to uncover patterns and hotspots in crime occurrences.

Inspired by a role, involving GIS software to map data, this project builds upon that experience by integrating geospatial data processing with clustering algorithms. It provides a comprehensive pipeline from data cleaning and preprocessing to clustering and interactive visualisation.

## Features 🔧

- **Data Processing**: Cleans raw crime data and performs joins with ward boundaries and population data.
- **Clustering**: Uses the DBSCAN algorithm to identify clusters of high crime density based on geographic coordinates.
- **Visualisation**: Creates maps using Folium to display crime clusters, cluster centers and heatmaps.
- **Utility Functions**: Includes functions for data type optimisation, handling missing values and geospatial calculations to improve efficiency and accuracy.

## Key Modules 💻

- **dataprocessing.py**: Functions for cleaning and preprocessing data, including spatial joins and merging datasets.
- **clustering.py**: Contains the clustering logic using DBSCAN and functions to analyse cluster results.
- **visual.py**: Functions to generate interactive maps for crime clusters and heatmaps.
- **utils.py**: Utility functions for data optimisation, handling missing values and geospatial calculations.

## Outputs

  - Cluster centers map (`cluster_centers_map.html`)
  - Crime heatmap (`crime_heatmap.html`)
  - Ward-level map (`wards_interactive_map.html`)

Note: `crime_clusters_map.html` is no longer generated due to file size.

## PSNI Ingestion

Run the CLI to fetch the latest PSNI street-level archive and overwrite the master CSV:

```bash
python src/ingest_psni.py
```

To fetch a specific month:

```bash
python src/ingest_psni.py --date 2024-01
```

By default the ingest script also appends each month into a historical dataset:

- Master file: `data/raw/crime_data.csv`
- Historical file: `data/processed/crime_history.csv`

## Hotspot Map

Build a fast, ward-level hotspot map from the historical dataset. This is much faster than plotting every crime point.

```bash
python src/hotspot_map.py --months-back 24
```

You can also set an explicit range:

```bash
python src/hotspot_map.py --start 2022-01 --end 2024-01
```

Outputs:

- `outputs/ward_hotspots_map.html`
- `outputs/ward_hotspots_summary.csv`

## Trend Reports

Generate ward and crime-type trend summaries (default 24 months):

```bash
python src/trend_report.py
```

Outputs:

- `outputs/ward_trends.csv`
- `outputs/crime_type_trends.csv`

## CLI Menu

Use the interactive CLI menu to ingest data, build maps, and view trend summaries:

```bash
python src/cli_menu.py
```

## Main Pipeline CLI

Run the main pipeline with an interactive prompt. Choose to run the full pipeline once, or enter flags and run repeatedly, then exit when finished.

```bash
python src/main.py
```

Non-interactive options:

```bash
python src/main.py --run-all
python src/main.py --no-prompt
```

Example flags in the prompt:

```bash
--eps-km 1.0 --min-samples 10
```

## GUI Scaffold (FastAPI + React)

Backend (FastAPI):

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r backend\\requirements.txt
uvicorn backend.app.main:app --reload --port 8000
```

Frontend (React + Vite):

```bash
cd frontend
npm install
npm run dev
```

If the API runs on a different host/port, set `VITE_API_URL` (e.g. `http://localhost:8000`).

## Future Work 🤔

I look forward to working on this project with my enhanced skill set that I have developed over the past year! I have tidied the project up for publishing to GitHub, however I want to expand the maps to create a more interactive interface and UI, along with developing more robust data ingestion pipeline to enable for iterative updates that don't rely on static files stored locally.  

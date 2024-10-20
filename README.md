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

## Outputs 🗺️

  - Crime clusters map (`crime_clusters_map.html`)
  - Cluster centers map (`cluster_centers_map.html`)
  - Crime heatmap (`crime_heatmap.html`)

## Future Work 🤔

I look forward to working on this project with my enhanced skill set that I have developed over the past year! I have tidied the project up for publishing to GitHub, however I want to expand the maps to create a more interactive interface and UI, along with developing more robust data ingestion pipeline to enable for iterative updates that don't rely on static files stored locally.  

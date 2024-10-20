from dataprocessing import clean_data, load_and_preprocess_data
from clustering import (
    apply_dbscan,
    identify_high_density_areas,
    get_cluster_centers,
    count_crimes_per_cluster,
    merge_cluster_info,
)
from visual import (
    plot_clusters_on_map,
    plot_cluster_centers,
    plot_crime_heatmap,
)
from utils import optimise_data_types, calculate_crime_rate, handle_missing_values
import pandas as pd
import os

# TODO - build interface for user 
# TODO - build pipeline for PSNI website
# TODO - ROI crime data
# TODO - interacctive map for user - county/ward clickable 
# TODO - deploy to web
# TODO - add logging
# TODO - add tests
# TODO - add requirements.txt

def main():
    # file paths
    shapefile_path = 'data/raw/OSNI_Open_Data_-_Largescale_Boundaries_-_Wards_(2012)/OSNI_Open_Data_-_Largescale_Boundaries_-_Wards_(2012).shp'
    crime_data_path = 'data/raw/crime_data.csv'
    population_data_path = 'data/raw/population_data.csv'
    cleaned_crime_data_path = 'data/processed/cleaned_crime_data.csv'
    merged_data_path = 'data/processed/merged_data.csv'
    cluster_info_path = 'outputs/cluster_info.csv'

    # make sure output directories exist
    os.makedirs('data/processed', exist_ok=True)
    os.makedirs('outputs', exist_ok=True)

    print("Starting the data cleaning process")
    
    # Clean data
    cleaned_crime_data = clean_data(shapefile_path, crime_data_path, cleaned_crime_data_path)

    print("Optimising data types")
    
    # Optimise data types
    cleaned_crime_data = optimise_data_types(cleaned_crime_data)
    cleaned_crime_data.to_csv(cleaned_crime_data_path, index=False)

    print("Handling missing values")
    
    # Handle missing values
    cleaned_crime_data = handle_missing_values(cleaned_crime_data, strategy='mean')

    print("Loading and preprocessing data for model")
    
    # Load and preprocess data for modeling
    merged_data = load_and_preprocess_data(
        shapefile_path, cleaned_crime_data_path, population_data_path, output_csv=merged_data_path
    )

    print("Calculating crime rates")
    
    # Calculate crime rate per 100,000 people
    merged_data['CrimeRatePer100kPeople'] = merged_data.apply(
        lambda row: calculate_crime_rate(row['NumberOfCrimes'], row['Population']),
        axis=1
    )

    print("Optimising merged data types")
    
    # Optimise data types for the merged data
    merged_data = optimise_data_types(merged_data)
    merged_data.to_csv(merged_data_path, index=False)

    print("Getting coordinates for clustering")
    
    # Extract coordinates
    coordinates = cleaned_crime_data[['Longitude', 'Latitude']].values

    print("Applying DBSCAN clustering")
    
    # apply DBSCAN algo
    labels = apply_dbscan(coordinates, eps_km=0.5, min_samples=5)

    print("Identifying high crime areas")
    
    # identify high-density crime areas
    clusters = identify_high_density_areas(cleaned_crime_data, labels)

    print("Calculating cluster centers and crime numbers")
    
    # cluster centers and counts
    cluster_centers = get_cluster_centers(clusters)
    cluster_counts = count_crimes_per_cluster(clusters)
    cluster_info = merge_cluster_info(cluster_centers, cluster_counts)

    # save the cluster info to csv file
    cluster_info.to_csv(cluster_info_path, index=False)
    print(f"Cluster information saved to '{cluster_info_path}'")

    print("Generating maps and visuals!")
    
    # visuals - build interface for them next 
    # TODO
    plot_clusters_on_map(clusters, output_path='outputs/crime_clusters_map.html')
    plot_cluster_centers(cluster_info, output_path='outputs/cluster_centers_map.html')
    plot_crime_heatmap(cleaned_crime_data, output_path='outputs/crime_heatmap.html')

    # success message! 🎉
    print("All tasks completed successfully.")


if __name__ == '__main__':
    main()
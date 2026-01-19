import numpy as np
from sklearn.cluster import DBSCAN
import pandas as pd

def apply_dbscan(coordinates, eps_km=0.5, min_samples=5):
    # Convert degrees to radians
    coordinates_rad = np.radians(coordinates)
    
    # Earth's radius in kilometers
    earth_radius = 6371.0088  # Mean Earth radius
    
    # convert eps from kilometers to radians
    eps_rad = eps_km / earth_radius
    
    # apply DBSCAN
    db = DBSCAN(eps=eps_rad, min_samples=min_samples, metric='haversine').fit(coordinates_rad)
    labels = db.labels_
    return labels

def identify_high_density_areas(crime_data, labels):
    # add cluster labels to the crime data
    crime_data['Cluster'] = labels
    
    # filter out noise points (label = -1)
    clusters = crime_data[crime_data['Cluster'] != -1].copy()
    
    return clusters

def get_cluster_centers(clusters):
    # group by cluster label and calculate mean latitude and longitude
    cluster_centers = clusters.groupby('Cluster').agg({
        'Latitude': 'mean',
        'Longitude': 'mean'
    }).reset_index()
    
    return cluster_centers

def count_crimes_per_cluster(clusters):
    cluster_counts = clusters['Cluster'].value_counts().reset_index()
    cluster_counts.columns = ['Cluster', 'CrimeCount']
    return cluster_counts

def merge_cluster_info(cluster_centers, cluster_counts):
    cluster_info = pd.merge(cluster_centers, cluster_counts, on='Cluster')
    return cluster_info

def calculate_cluster_metrics(labels):
    labels = np.asarray(labels)
    total_points = len(labels)
    noise_points = int((labels == -1).sum())
    cluster_labels = labels[labels != -1]
    if cluster_labels.size == 0:
        return {
            "total_points": total_points,
            "noise_points": noise_points,
            "noise_share": 1.0 if total_points else 0.0,
            "cluster_count": 0,
            "avg_cluster_size": 0,
            "median_cluster_size": 0,
            "min_cluster_size": 0,
            "max_cluster_size": 0,
        }

    counts = pd.Series(cluster_labels).value_counts()
    return {
        "total_points": total_points,
        "noise_points": noise_points,
        "noise_share": noise_points / total_points if total_points else 0.0,
        "cluster_count": int(counts.shape[0]),
        "avg_cluster_size": float(counts.mean()),
        "median_cluster_size": float(counts.median()),
        "min_cluster_size": int(counts.min()),
        "max_cluster_size": int(counts.max()),
    }

def dbscan_sweep(coordinates, eps_km_list, min_samples_list):
    results = []
    for eps_km in eps_km_list:
        for min_samples in min_samples_list:
            labels = apply_dbscan(coordinates, eps_km=eps_km, min_samples=min_samples)
            metrics = calculate_cluster_metrics(labels)
            results.append(
                {
                    "eps_km": eps_km,
                    "min_samples": min_samples,
                    **metrics,
                }
            )
    return pd.DataFrame(results)

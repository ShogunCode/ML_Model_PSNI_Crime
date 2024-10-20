import folium
import geopandas as gpd
import pandas as pd
import matplotlib.cm as cm
import matplotlib.colors as colors

def plot_clusters_on_map(clusters, output_path='outputs/crime_clusters_map.html'):
    # initialise the map centered around the mean coordinates
    mean_lat = clusters['Latitude'].mean()
    mean_lon = clusters['Longitude'].mean()
    m = folium.Map(location=[mean_lat, mean_lon], zoom_start=10, tiles='CartoDB Positron')
    
    # get the  cluster labels
    cluster_labels = clusters['Cluster'].unique()
    num_clusters = len(cluster_labels)
    
    # Create color map
    colormap = cm.get_cmap('tab20', num_clusters)
    cluster_colors = {label: colors.rgb2hex(colormap(i)) for i, label in enumerate(cluster_labels)}
    
    # add the clusters to map
    for label in cluster_labels:
        cluster_data = clusters[clusters['Cluster'] == label]
        
        # create a feature group for each cluster
        cluster_group = folium.FeatureGroup(name=f'Cluster {label}', show=True)
        
        for idx, row in cluster_data.iterrows():
            folium.CircleMarker(
                location=[row['Latitude'], row['Longitude']],
                radius=3,
                color=cluster_colors[label],
                fill=True,
                fill_color=cluster_colors[label],
                fill_opacity=0.7,
                popup=folium.Popup(html=f"""
                    <b>Crime Type:</b> {row['Crime type']}<br>
                    <b>Location:</b> {row['Location']}<br>
                    <b>Cluster:</b> {row['Cluster']}
                """, max_width=250)
            ).add_to(cluster_group)
        
        # add the cluster group to the map
        cluster_group.add_to(m)
    
    # add layer control to toggle clusters
    folium.LayerControl().add_to(m)
    
    # aave the map to an HTML file
    m.save(output_path)
    print(f"Map saved to '{output_path}'")

def plot_cluster_centers(cluster_info, output_path='outputs/cluster_centers_map.html'):
    # initialise the map centered around the mean coordinates
    mean_lat = cluster_info['Latitude'].mean()
    mean_lon = cluster_info['Longitude'].mean()
    m = folium.Map(location=[mean_lat, mean_lon], zoom_start=10, tiles='CartoDB Positron')
    
    # create a color map
    num_clusters = cluster_info.shape[0]
    colormap = cm.get_cmap('tab20', num_clusters)
    cluster_colors = {label: colors.rgb2hex(colormap(i)) for i, label in enumerate(cluster_info['Cluster'])}
    
    for idx, row in cluster_info.iterrows():
        folium.CircleMarker(
            location=[row['Latitude'], row['Longitude']],
            radius=5 + (row['CrimeCount'] / 10),
            color=cluster_colors[row['Cluster']],
            fill=True,
            fill_color=cluster_colors[row['Cluster']],
            fill_opacity=0.7,
            popup=folium.Popup(html=f"""
                <b>Cluster:</b> {row['Cluster']}<br>
                <b>Crime Count:</b> {row['CrimeCount']}
            """, max_width=250)
        ).add_to(m)
    
    # save the map to an HTML file
    m.save(output_path)
    print(f"Cluster centers map has been saved to '{output_path}'")

def plot_crime_heatmap(crime_data, output_path='outputs/crime_heatmap.html'):
    from folium.plugins import HeatMap

    # initialise the map centered around the mean coordinates
    mean_lat = crime_data['Latitude'].mean()
    mean_lon = crime_data['Longitude'].mean()
    m = folium.Map(location=[mean_lat, mean_lon], zoom_start=10, tiles='CartoDB Dark_Matter')
    
    # prepare the data for heatmap
    heat_data = list(zip(crime_data['Latitude'], crime_data['Longitude']))
    
    #add heatmap layer
    HeatMap(heat_data, radius=8, max_zoom=13).add_to(m)
    
    #save the map to a HTML file
    m.save(output_path)
    print(f"Crime heatmap has been saved to '{output_path}'")

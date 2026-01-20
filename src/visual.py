import folium
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
    if cluster_info.empty:
        print("No cluster centers to plot; skipping cluster centers map.")
        return

    if "Latitude" not in cluster_info.columns or "Longitude" not in cluster_info.columns:
        raise ValueError("cluster_info must include Latitude and Longitude columns.")

    cluster_info = cluster_info.dropna(subset=["Latitude", "Longitude"])
    if cluster_info.empty:
        print("Cluster centers have no valid coordinates; skipping cluster centers map.")
        return

    # initialise the map centered around the mean coordinates
    mean_lat = cluster_info['Latitude'].mean()
    mean_lon = cluster_info['Longitude'].mean()
    m = folium.Map(location=[mean_lat, mean_lon], zoom_start=10, tiles='CartoDB Positron')
    
    # create a color map
    cluster_labels = cluster_info['Cluster'].unique()
    num_clusters = len(cluster_labels)
    colormap = cm.get_cmap('tab20', num_clusters)
    cluster_colors = {label: colors.rgb2hex(colormap(i)) for i, label in enumerate(cluster_labels)}
    
    max_radius = 16
    min_radius = 4
    scale = 1.5

    for idx, row in cluster_info.iterrows():
        crime_count = row.get("CrimeCount", 0)
        if pd.isna(crime_count):
            crime_count = 0
        radius = min_radius + (float(crime_count) ** 0.5) * scale
        radius = max(min_radius, min(max_radius, radius))
        folium.CircleMarker(
            location=[row['Latitude'], row['Longitude']],
            radius=radius,
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

def plot_interactive_ward_map(wards_gdf, output_path='outputs/wards_interactive_map.html', simplify_tolerance=0.0003):
    if wards_gdf.empty:
        raise ValueError("wards_gdf is empty; cannot build interactive map.")

    # Ensure WGS84 for web mapping
    if wards_gdf.crs is not None and wards_gdf.crs.to_epsg() != 4326:
        wards = wards_gdf.to_crs(epsg=4326)
    else:
        wards = wards_gdf

    # Pick best-available columns for popups/tooltip
    preferred_fields = [
        ("Ward", "WARDNAME"),
        ("Ward Code", "WardCode_w"),
        ("Ward Code", "WardCode"),
        ("Threat Band", "RatingBand"),
        ("County", "COUNTY"),
        ("County", "County"),
        ("District", "LGDNAME"),
        ("District", "LGD_NAME"),
        ("Population", "Population"),
        ("Crimes", "NumberOfCrimes"),
        ("Annualized Rate/100k", "CrimeRatePer100kPeople"),
    ]

    fields = []
    aliases = []
    for label, column in preferred_fields:
        if column in wards.columns and column not in fields:
            fields.append(column)
            aliases.append(f"{label}: ")

    if not fields:
        fields = [col for col in wards.columns if col != "geometry"][:5]
        aliases = [f"{col}: " for col in fields]

    # Reduce payload size for faster map rendering
    map_gdf = wards[fields + ["geometry"]].copy()
    map_gdf["geometry"] = map_gdf["geometry"].simplify(simplify_tolerance, preserve_topology=True)

    m = folium.Map(tiles="CartoDB Positron")

    band_col = None
    for candidate in ("RatingBand", "rating_band", "Rating Band"):
        if candidate in map_gdf.columns:
            band_col = candidate
            break

    if band_col is None or map_gdf[band_col].isna().all():
        if "CrimeRatePer100kPeople" in map_gdf.columns:
            rates = pd.to_numeric(map_gdf["CrimeRatePer100kPeople"], errors="coerce")
            percentiles = rates.rank(pct=True)
            map_gdf["MapBand"] = pd.cut(
                percentiles,
                bins=[-float("inf"), 0.55, 0.7, 0.85, float("inf")],
                labels=["Stable", "Watch", "Elevated", "High"],
            ).astype(str)
            band_col = "MapBand"

    band_colors = {
        "high": "#fb7185",
        "elevated": "#facc15",
        "watch": "#4ef3c4",
        "stable": "#94a3b8",
        "unknown": "#64748b",
    }

    def _style(_feature):
        fill_color = "#3b82f6"
        if band_col:
            band_value = _feature.get("properties", {}).get(band_col)
            if band_value:
                fill_color = band_colors.get(str(band_value).lower(), fill_color)
            else:
                fill_color = band_colors.get("unknown", fill_color)
        return {
            "fillColor": fill_color,
            "color": "#0f172a",
            "weight": 1,
            "fillOpacity": 0.55,
        }

    def _highlight(_feature):
        return {
            "weight": 2,
            "color": "#111827",
            "fillOpacity": 0.6,
        }

    folium.GeoJson(
        map_gdf,
        name="Wards",
        style_function=_style,
        highlight_function=_highlight,
        tooltip=folium.GeoJsonTooltip(fields=fields, aliases=aliases, sticky=False),
        popup=folium.GeoJsonPopup(fields=fields, aliases=aliases, labels=True),
    ).add_to(m)

    bounds = map_gdf.total_bounds
    if bounds is not None and len(bounds) == 4:
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    folium.LayerControl().add_to(m)
    m.save(output_path)
    print(f"Interactive ward map has been saved to '{output_path}'")

import pandas as pd
import numpy as np

def optimise_data_types(df):
    # downcast floats
    float_cols = df.select_dtypes(include=['float'])
    for col in float_cols.columns:
        df[col] = pd.to_numeric(df[col], downcast='float')
    
    # downcast integers
    int_cols = df.select_dtypes(include=['int'])
    for col in int_cols.columns:
        df[col] = pd.to_numeric(df[col], downcast='integer')
    
    # convert object types to categorical if appropriate
    object_cols = df.select_dtypes(include=['object'])
    for col in object_cols.columns:
        num_unique_values = df[col].nunique()
        num_total_values = len(df[col])
        if num_unique_values / num_total_values < 0.5:
            df[col] = df[col].astype('category')
    
    return df

def calculate_crime_rate(number_of_crimes, population):
    if population == 0 or np.isnan(population):
        return 0.0
    else:
        return (number_of_crimes / population) * 100000

def handle_missing_values(df, strategy='mean', columns=None):
    if columns is None:
        if strategy in ['mean', 'median']:
            # apply to numeric columns only
            columns = df.select_dtypes(include=['number']).columns
        elif strategy == 'mode':
            #apply to all columns
            columns = df.columns
        else:
            columns = df.columns

    if strategy == 'mean':
        for col in columns:
            df[col].fillna(df[col].mean(), inplace=True)
    elif strategy == 'median':
        for col in columns:
            df[col].fillna(df[col].median(), inplace=True)
    elif strategy == 'mode':
        for col in columns:
            df[col].fillna(df[col].mode()[0], inplace=True)
    elif strategy == 'drop':
        df.dropna(subset=columns, inplace=True)
    else:
        raise ValueError("Choose 'mean', 'median', 'mode' or 'drop'.")

    return df

def haversine_distance(coord1, coord2):
    # convert decimal degrees to radians
    lon1, lat1 = np.radians(coord1)
    lon2, lat2 = np.radians(coord2)
    
    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2.0)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0)**2
    c = 2 * np.arcsin(np.sqrt(a))
    
    # earth radius in kilometers
    r = 6371.0088
    return c * r

def calculate_area_sq_km(geometry_series):
    # Convert from square meters to square kilometers
    areas = geometry_series.area / 1e6  
    return areas

def extract_coordinates_from_geometry(geometry):
    return geometry.x, geometry.y
